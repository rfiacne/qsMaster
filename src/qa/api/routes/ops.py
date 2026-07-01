"""运维探针路由 — /metrics, /health, /ready"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter

from qa.api.dependencies import get_store
from qa.config.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/qa")


@router.get("/metrics")
async def metrics():
    """可观测性指标"""
    from qa.pipelines.components.tracing import get_metrics

    return get_metrics().snapshot().to_dict()


@router.get("/health")
async def health():
    """健康检查（轻量）"""
    return {"status": "ok", "version": "0.1.0"}


@router.get("/ready")
async def readiness():
    """就绪探针 — 深度检查依赖服务健康状况

    Returns:
        200: 所有依赖正常
        503: 依赖异常（返回异常详情）
    """
    checks: dict[str, Any] = {
        "status": "ok",
        "version": "0.1.0",
        "checks": {},
    }
    all_healthy = True

    # 1) 检查向量存储
    try:
        store = get_store()
        chunk_count = await asyncio.to_thread(store.count_chunks)
        checks["checks"]["store"] = {
            "status": "ok",
            "chunk_count": chunk_count,
        }
    except Exception as e:
        checks["checks"]["store"] = {"status": "error", "detail": str(e)}
        all_healthy = False

    # 2) 检查嵌入服务（只做配置级检查，不实际调用 API）
    try:
        settings = get_settings()
        if settings.embedding.api_base_url:
            checks["checks"]["embedding"] = {
                "status": "ok",
                "backend": settings.embedding.backend,
                "api_base": settings.embedding.api_base_url,
            }
        else:
            checks["checks"]["embedding"] = {"status": "ok", "backend": "local"}
    except Exception as e:
        checks["checks"]["embedding"] = {"status": "error", "detail": str(e)}
        all_healthy = False

    # 3) 检查 LLM 配置
    try:
        settings = get_settings()
        checks["checks"]["llm"] = {
            "status": "ok",
            "model": settings.llm.model,
            "api_base": settings.llm.api_base_url,
            "has_api_key": bool(settings.llm.resolved_api_key),
        }
    except Exception as e:
        checks["checks"]["llm"] = {"status": "error", "detail": str(e)}
        all_healthy = False

    if not all_healthy:
        checks["status"] = "degraded"
        from fastapi.responses import JSONResponse

        return JSONResponse(content=checks, status_code=503)

    return checks
