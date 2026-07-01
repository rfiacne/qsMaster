"""
API Gateway 中间件 — 认证 + 限流 + 请求日志

功能:
  - require_api_key: FastAPI Depends() 依赖，校验 X-API-Key 请求头
  - RateLimiter: 内存滑动窗口限流（per API key）
  - log_request: 请求日志（method, path, latency, status）

用法:
    from qa.api.middleware import require_api_key, rate_limiter

    @app.post("/api/v1/qa/ask")
    async def ask(req: AskRequest, _key=Depends(require_api_key)):
        ...
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from threading import Lock
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import APIKeyHeader

from qa.config.settings import get_settings

logger = logging.getLogger(__name__)

# ─── API Key Auth ───────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# 不需要鉴权的路径前缀（只读安全操作 + 健康检查）
AUTH_BYPASS_PREFIXES = (
    "/api/v1/qa/health",
    "/api/v1/qa/status",
    "/api/v1/qa/metrics",
    "/docs",
    "/openapi.json",
)


async def require_api_key(
    request: Request,
    api_key: Annotated[str | None, Depends(_api_key_header)],
) -> str | None:
    """校验 API Key

    - 如果 server.api_keys 为空，跳过鉴权（向后兼容）
    - 如果路径在 AUTH_BYPASS_PREFIXES 中，跳过鉴权
    - 否则必须提供有效的 X-API-Key

    Returns:
        str | None: 验证通过的 API key（脱敏后用于日志）
    """
    settings = get_settings()
    configured_keys = settings.server.api_keys

    # 未配置密钥 = 跳过鉴权
    if not configured_keys:
        return None

    # 路径白名单
    path = request.url.path
    if any(path.startswith(prefix) for prefix in AUTH_BYPASS_PREFIXES):
        return None

    # 校验
    if not api_key or not any(secrets.compare_digest(api_key, k) for k in configured_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key


# ─── Rate Limiter ───────────────────────────────────────────


class RateLimiter:
    """内存滑动窗口限流器（per API key）

    线程安全。单实例部署足够用；多实例需换 Redis。
    """

    def __init__(self, rpm: int = 60, window_seconds: int = 60):
        self.rpm = rpm
        self.window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, key: str) -> tuple[bool, int, int]:
        """检查是否允许请求

        Returns:
            (allowed, remaining, limit)
        """
        if self.rpm <= 0:
            return True, self.rpm, self.rpm

        now = time.time()
        cutoff = now - self.window

        with self._lock:
            # 清理过期记录
            timestamps = self._requests[key]
            self._requests[key] = [t for t in timestamps if t > cutoff]
            timestamps = self._requests[key]

            count = len(timestamps)
            remaining = max(0, self.rpm - count)

            if count >= self.rpm:
                return False, 0, self.rpm

            timestamps.append(now)
            return True, remaining - 1, self.rpm

    def reset(self, key: str | None = None) -> None:
        """重置限流计数（测试用）"""
        with self._lock:
            if key:
                self._requests.pop(key, None)
            else:
                self._requests.clear()


# 全局实例（由 server.py 初始化）
_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """获取限流器实例"""
    global _rate_limiter
    if _rate_limiter is None:
        settings = get_settings()
        _rate_limiter = RateLimiter(rpm=settings.server.rate_limit_rpm)
    return _rate_limiter


async def check_rate_limit(
    request: Request,
    api_key: Annotated[str | None, Depends(require_api_key)],
) -> None:
    """限流检查（作为 Depends 链）

    在 require_api_key 之后调用，确保已鉴权。
    超限返回 429，并在响应头设置限流信息。
    """
    settings = get_settings()
    if settings.server.rate_limit_rpm <= 0:
        return

    # 使用 api_key 或 client IP 作为限流 key
    key = api_key or (request.client.host if request.client else "unknown")
    limiter = get_rate_limiter()
    allowed, remaining, limit = limiter.check(key)

    # 将限流信息存入 request state，供响应中间件读取
    request.state.rate_limit = {
        "limit": limit,
        "remaining": remaining,
    }

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "Retry-After": "60",
            },
        )


# ─── Request Logger ─────────────────────────────────────────


def mask_key(key: str | None) -> str:
    """脱敏 API key（仅显示前4位）"""
    if not key:
        return "(none)"
    if len(key) <= 4:
        return "****"
    return key[:4] + "****"


def get_request_id(request: Request) -> str:
    """获取或生成请求 ID（优先使用调用方传入的 X-Request-ID）"""
    req_id = request.headers.get("X-Request-ID")
    if not req_id:
        req_id = str(uuid.uuid4())[:8]
    request.state.request_id = req_id
    return req_id


def struct_log(
    logger_instance: logging.Logger,
    level: int,
    message: str,
    request_id: str = "",
) -> None:
    """结构化日志 — JSON 格式输出（带 request_id）"""
    if request_id:
        logger_instance.log(level, message, extra={"request_id": request_id})
    else:
        logger_instance.log(level, message)


async def log_request_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """请求日志中间件

    记录: method, path, api_key (masked), latency, status_code, request_id
    """
    req_id = get_request_id(request)
    start = time.time()

    # 提取 API key（不触发鉴权，仅用于日志）
    api_key = request.headers.get("X-API-Key")

    response = await call_next(request)

    latency_ms = (time.time() - start) * 1000

    # 添加限流响应头
    if hasattr(request.state, "rate_limit"):
        rl = request.state.rate_limit
        response.headers["X-RateLimit-Limit"] = str(rl["limit"])
        response.headers["X-RateLimit-Remaining"] = str(rl["remaining"])

    # 添加请求 ID 响应头
    response.headers["X-Request-ID"] = req_id

    struct_log(
        logger,
        logging.INFO,
        f"{request.method} {request.url.path} "
        f"key={mask_key(api_key)} status={response.status_code} "
        f"latency={latency_ms:.1f}ms",
        request_id=req_id,
    )

    return response
