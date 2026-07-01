"""
API 共享依赖 — 单例、请求模型、App 工厂

供路由模块和 server.py 使用。
"""

from __future__ import annotations

import logging
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from qa.api.middleware import log_request_middleware
from qa.config.settings import get_settings
from qa.pipelines.indexing import IndexingPipeline
from qa.stores.turbovec_store import create_store_manager

# 确保 src 在路径中
_src = str(Path(__file__).resolve().parent.parent.parent)
if _src not in sys.path:
    sys.path.insert(0, _src)

logger = logging.getLogger(__name__)


# ─── 请求/响应模型 ─────────────────────────────────────


class AskRequest(BaseModel):
    """问答请求"""

    question: str
    top_k: int = 5
    filters: dict[str, Any] | None = None
    no_llm: bool = False
    session_id: str = ""


class SearchRequest(BaseModel):
    """检索请求"""

    query: str
    top_k: int = 20


# ─── 延迟初始化单例 ─────────────────────────────────────


_store = None
_query_pipeline = None
_index_pipeline = None
_bm25_index = None
_lock = threading.Lock()


def get_store():
    """获取（惰性初始化）向量存储单例"""
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                settings = get_settings()
                _store = create_store_manager(
                    bit_width=settings.vector_store.bit_width,
                    similarity_function=settings.vector_store.similarity_function,
                    persist_path=settings.vector_store.persist_path,
                )
    return _store


def get_bm25_index():
    """获取（惰性构建）全量 BM25 索引单例。

    供 get_query_pipeline() 与 warmup() 共享，避免预热结果被丢弃
    （M6 review finding #1/#9）。
    """
    global _bm25_index
    if _bm25_index is None:
        with _lock:
            if _bm25_index is None:
                from qa.pipelines.factory import build_bm25_index

                _bm25_index = build_bm25_index(get_settings(), get_store())
    return _bm25_index


def reset_runtime_singletons():
    """重建索引后调用：清空 pipeline/BM25/缓存单例，使下次请求重建。

    索引版本变更后，旧 BM25 索引与查询缓存均失效。
    """
    global _query_pipeline, _bm25_index
    with _lock:
        _query_pipeline = None
        _bm25_index = None
    # 查询缓存按 store_version 自动失效，无需手动清理


def get_query_pipeline():
    """获取（惰性构建）查询 pipeline 单例"""
    global _query_pipeline
    if _query_pipeline is None:
        with _lock:
            if _query_pipeline is None:
                from qa.pipelines.factory import build_query_pipeline

                _query_pipeline = build_query_pipeline(get_settings(), get_store())
    return _query_pipeline


def get_index_pipeline():
    """获取（惰性构建）索引 pipeline 单例"""
    global _index_pipeline
    if _index_pipeline is None:
        with _lock:
            if _index_pipeline is None:
                settings = get_settings()
                _index_pipeline = IndexingPipeline(
                    store_manager=get_store(),
                    block_sizes=settings.retrieval.block_sizes,
                    ocr_enabled=settings.indexing.ocr_enabled,
                    ocr_backend=settings.indexing.ocr_backend,
                    doc_timeout_sec=settings.indexing.doc_timeout_seconds,
                    embed_timeout_sec=float(settings.embedding.timeout_seconds),
                )
    return _index_pipeline


# ─── Warmup + Lifespan ─────────────────────────────────


async def warmup():
    """服务启动预热

    预加载 store、BM25 索引、embedder，消除首请求延迟尖峰。
    预热失败不阻塞服务启动。所有阻塞调用走 to_thread，避免卡住事件循环。
    """
    import asyncio

    logger.info("🚀 服务启动预热中...")

    # 1) 预热 Store（加载向量索引）
    try:
        store = get_store()
        chunk_count = await asyncio.to_thread(store.count_chunks)
        logger.info(f"  ✅ Store 加载完成: {chunk_count} chunks")
    except Exception as e:
        logger.warning(f"  ⚠️ Store 预热失败（不阻塞启动）: {e}")

    # 2) 预热 BM25 索引（复用单例，避免预热结果被丢弃）
    try:
        settings = get_settings()
        if settings.retrieval.use_hybrid:
            store = get_store()
            if await asyncio.to_thread(store.count_chunks) > 0:
                bm25_idx = await asyncio.to_thread(get_bm25_index)
                if bm25_idx is not None and bm25_idx.is_built:
                    logger.info(f"  ✅ BM25 索引预热完成: {bm25_idx.total_docs} 文档")
                else:
                    logger.info("  ℹ️  BM25 索引未构建（知识库为空）")
            else:
                logger.info("  ℹ️  跳过 BM25 预热（知识库为空）")
    except Exception as e:
        logger.warning(f"  ⚠️ BM25 预热失败（不阻塞启动）: {e}")

    # 3) 预热 Embedder
    try:
        from qa.pipelines.components.embedder import embed_query

        test_emb = await asyncio.to_thread(embed_query, "预热测试")
        if test_emb:
            logger.info(f"  ✅ Embedder 预热完成: dim={len(test_emb)}")
    except Exception as e:
        logger.warning(f"  ⚠️ Embedder 预热失败（不阻塞启动）: {e}")

    logger.info("🏁 服务启动预热完成")


@asynccontextmanager
async def lifespan(app_instance):
    """FastAPI lifespan 上下文管理器"""
    await warmup()
    yield


# ─── App 工厂 ─────────────────────────────────────────


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。

    工厂模式确保组件以正确顺序初始化：
    1. 创建 app 实例（注册 lifespan）
    2. 注册中间件
    3. 包含所有路由模块
    """
    app = FastAPI(title="Securities QA Agent API", version="0.1.0", lifespan=lifespan)

    # ── 请求日志中间件（最外层，记录所有请求） ──
    @app.middleware("http")
    async def request_logging_middleware(request, call_next):
        return await log_request_middleware(request, call_next)

    # ── CORS — 从配置读取允许的来源 ──
    _origins = get_settings().server.allowed_origins
    _has_wildcard = "*" in _origins
    if _has_wildcard:
        logger.warning("CORS: allowed_origins contains '*' — allow_credentials disabled for safety")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=not _has_wildcard,
        allow_methods=["*"],
        allow_headers=["*", "X-API-Key"],
    )

    # ── 路由注册 ──
    from qa.api.routes import answers, ops, qa, reviews, sessions, upload

    app.include_router(qa.router)
    app.include_router(sessions.router)
    app.include_router(upload.router)
    app.include_router(answers.router)
    app.include_router(reviews.router)
    app.include_router(ops.router)

    return app
