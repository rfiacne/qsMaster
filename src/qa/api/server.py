"""
Securities QA Agent — FastAPI 后端服务

为前端 index.html 提供 REST API。
启动: uvicorn qa.api.server:app --host 0.0.0.0 --port 8001
或者: python -m qa.api.server
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from qa.api.dependencies import create_app
from qa.config.settings import get_settings

logger = logging.getLogger(__name__)


def _configure_structured_logging() -> None:
    """配置结构化日志（JSON 格式 + 请求 ID 传播）

    仅当未通过 --log-config 指定自定义配置时生效。
    使用 JSON 格式方便日志采集系统（ELK / Loki）解析。
    """
    if logging.getLogger().hasHandlers():
        # 已有配置（如 uvicorn 默认），不覆盖
        return

    import datetime as _dt

    class JSONFormatter(logging.Formatter):
        """JSON 日志格式化器"""

        def format(self, record: logging.LogRecord) -> str:
            log_entry: dict[str, object] = {
                "ts": _dt.datetime.fromtimestamp(record.created, tz=_dt.UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info and record.exc_info[0]:
                log_entry["exception"] = self.formatException(record.exc_info)
            if hasattr(record, "request_id"):
                log_entry["request_id"] = record.request_id
            return json.dumps(log_entry, ensure_ascii=True)

    _handler = logging.StreamHandler()
    _handler.setFormatter(JSONFormatter())
    root_logger = logging.getLogger()
    root_logger.addHandler(_handler)
    root_logger.setLevel(logging.INFO)


# 在模块加载时配置结构化日志
# 注：uvicorn 启动时已有日志配置，此调用会被 hasHandlers() 守卫跳过
# 直接启动时（python -m qa.api.server）在 main() 中配置


# FastAPI 应用实例 — 保持 from qa.api.server import app 兼容
app = create_app()


# ─── 前端挂载 ─────────────────────────────────────────


def _mount_frontend(app_instance):
    """挂载前端静态文件，禁用缓存"""
    frontend_dir = Path(__file__).resolve().parent.parent.parent.parent / "frontend"
    if not frontend_dir.exists():
        return
    try:
        from fastapi.staticfiles import StaticFiles
        from starlette.middleware.base import BaseHTTPMiddleware

        class NoCacheMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                response = await call_next(request)
                if request.url.path in ("/", "/index.html", "/marked.min.js"):
                    response.headers["Cache-Control"] = "no-store, must-revalidate"
                    response.headers["Pragma"] = "no-cache"
                    response.headers["Expires"] = "0"
                return response

        app_instance.mount(
            "/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend"
        )
        app_instance.add_middleware(NoCacheMiddleware)
        logger.info(f"前端静态文件已挂载: {frontend_dir}")
    except Exception as e:
        logger.warning(f"前端挂载失败: {e}")


# 模块最末尾挂载前端（所有 API 路由已注册完毕）
_mount_frontend(app)


# ─── 直接启动 ─────────────────────────────────────────


def main():
    # 直接启动时（非 uvicorn 方式）配置结构化 JSON 日志
    _configure_structured_logging()

    import uvicorn

    settings = get_settings()
    host = settings.server.host
    port = settings.server.port
    reload = settings.server.reload

    print(f"Securities QA Agent API 服务启动: http://{host}:{port}")
    print(f"前端: http://{host}:{port}   API: http://{host}:{port}/api/v1")
    uvicorn.run("qa.api.server:app", host=host, port=port, reload=reload, log_level="info")


if __name__ == "__main__":
    main()
