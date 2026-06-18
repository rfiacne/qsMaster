"""
Pipeline 组件工厂 — 集中构建查询改写器/缓存/BM25 索引

server.py / ask.py / chat.py / compare.py 共享此工厂，
避免四处复制 wiring 逻辑导致漂移（M6 review finding #1）。
"""

from __future__ import annotations

import logging
from typing import Any

from qa.config.settings import Settings, get_settings
from qa.stores.turbovec_store import StoreManager

logger = logging.getLogger(__name__)


def build_query_rewriter(settings: Settings) -> Any | None:
    """构建 QueryRewriter；未启用返回 None。"""
    if not settings.query_rewrite.enabled:
        return None
    from qa.pipelines.components.query_rewriter import QueryRewriter

    return QueryRewriter(
        term_map_path=settings.query_rewrite.term_map_path,
        model=settings.query_rewrite.model,
        timeout_seconds=settings.query_rewrite.timeout_seconds,
        enabled=settings.query_rewrite.enabled,
    )


def build_query_cache(settings: Settings, store_manager: StoreManager) -> Any | None:
    """构建 QueryCache；未启用返回 None。"""
    if not settings.query_cache.enabled:
        return None
    from qa.pipelines.components.query_cache import QueryCache

    return QueryCache(
        max_size=settings.query_cache.max_size,
        ttl_seconds=settings.query_cache.ttl_seconds,
        enabled=settings.query_cache.enabled,
        store_manager=store_manager,
    )


def build_bm25_index(settings: Settings, store_manager: StoreManager) -> Any | None:
    """构建/加载全量 BM25 索引；未启用混合检索或知识库为空返回 None。

    失败不抛出（返回 None），调用方降级为纯向量检索。
    """
    if not settings.retrieval.use_hybrid:
        return None
    if store_manager.count_chunks() == 0:
        return None
    try:
        from qa.pipelines.components.bm25_index import load_or_build

        idx = load_or_build(store_manager)
        if idx.is_built:
            logger.info(f"BM25 索引就绪: {idx.total_docs} 文档")
            return idx
        logger.info("BM25 索引未构建（知识库为空），降级纯向量检索")
        return None
    except Exception as e:
        logger.warning(f"BM25 索引加载失败（降级纯向量检索）: {e}")
        return None


def build_query_components(
    settings: Settings | None = None,
    store_manager: StoreManager | None = None,
) -> tuple[Any | None, Any | None, Any | None]:
    """一次性构建 (query_rewriter, query_cache, bm25_index) 三元组。

    Args:
        settings: 配置（默认 get_settings()）
        store_manager: 存储管理器（必填，用于 BM25/缓存版本监听）

    Returns:
        (rewriter, cache, bm25_index)，未启用的项为 None。
    """
    if settings is None:
        settings = get_settings()
    if store_manager is None:
        raise ValueError("store_manager is required for build_query_components")

    return (
        build_query_rewriter(settings),
        build_query_cache(settings, store_manager),
        build_bm25_index(settings, store_manager),
    )
