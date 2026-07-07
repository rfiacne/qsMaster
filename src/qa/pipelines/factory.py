"""
Pipeline 组件工厂 — 集中构建问答查询全链路组件

server.py / ask.py / chat.py 共享此工厂，
避免四处复制 wiring 逻辑导致漂移（M6 review finding #1）。
"""

from __future__ import annotations

import logging
from typing import Any

from qa.config.settings import Settings, get_settings
from qa.stores.turbovec_store import StoreManager

logger = logging.getLogger(__name__)


def _build_early_exit(settings: Settings) -> Any | None:
    if not settings.early_exit.enabled:
        return None
    from qa.pipelines.components.early_exit import EarlyExitMatcher

    return EarlyExitMatcher(
        store_path=settings.early_exit.store_path,
        fuzzy_threshold=settings.early_exit.fuzzy_threshold,
        enabled=settings.early_exit.enabled,
    )


def _build_faithfulness(settings: Settings) -> Any | None:
    if not settings.faithfulness.enabled:
        return None
    from qa.pipelines.components.faithfulness import FaithfulnessEvaluator

    return FaithfulnessEvaluator(
        enabled=True,
        threshold=settings.faithfulness.threshold,
        max_claims=settings.faithfulness.max_claims,
        judge_model=settings.faithfulness.judge_model,
        judge_api_base_url=settings.faithfulness.judge_api_base_url,
        mode=settings.faithfulness.mode,
    )


def build_store(settings: Settings | None = None) -> StoreManager:
    if settings is None:
        settings = get_settings()
    from qa.stores.turbovec_store import create_store_manager

    return create_store_manager(
        bit_width=settings.vector_store.bit_width,
        similarity_function=settings.vector_store.similarity_function,
        persist_path=settings.vector_store.persist_path,
    )


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
        concept_disambig_enabled=settings.query_rewrite.concept_disambig_enabled,
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
    """一次性构建 (query_rewriter, query_cache, bm25_index) 三元组。"""
    if settings is None:
        settings = get_settings()
    if store_manager is None:
        raise ValueError("store_manager is required for build_query_components")

    return (
        build_query_rewriter(settings),
        build_query_cache(settings, store_manager),
        build_bm25_index(settings, store_manager),
    )


def _build_reranker(settings: Settings) -> Any | None:
    if not settings.rerank.enabled:
        return None
    from qa.pipelines.components.reranker import Reranker

    return Reranker(
        model=settings.rerank.model,
        api_base_url=settings.rerank.api_base_url,
        api_key=settings.rerank.resolved_api_key,
        top_k=settings.rerank.top_k,
        timeout=settings.rerank.timeout_seconds,
    )


def build_query_pipeline(
    settings: Settings | None = None,
    store_manager: StoreManager | None = None,
    top_k: int | None = None,
) -> Any:
    """完整构建 QueryPipeline（问答全链路）。

    一次性完成: store → early_exit → faithfulness → review → audit →
    otel → query_rewriter → query_cache → bm25 → reranker → QueryPipeline。

    Args:
        settings: 配置（默认 get_settings()）
        store_manager: 存储管理器（默认 build_store()）
        top_k: 检索数（覆盖 settings.retrieval.top_k）
    """
    if settings is None:
        settings = get_settings()
    if store_manager is None:
        store_manager = build_store(settings)

    early_exit_matcher = _build_early_exit(settings)
    faithfulness_evaluator = _build_faithfulness(settings)
    reranker = _build_reranker(settings)

    from qa.pipelines.components.review_queue import ReviewWorkflow

    review_workflow = ReviewWorkflow()
    review_workflow.ensure_loaded()

    from qa.pipelines.components.audit_logger import AuditStore

    audit_store = AuditStore()

    from qa.pipelines.components.tracing import init_tracing

    init_tracing(
        service_name=settings.otel.service_name,
        otlp_endpoint=settings.otel.endpoint,
        enabled=settings.otel.enabled,
    )

    query_rewriter, query_cache, bm25_index = build_query_components(settings, store_manager)

    from qa.pipelines.querying import QueryPipeline

    return QueryPipeline(
        store_manager=store_manager,
        top_k=top_k or settings.retrieval.top_k,
        auto_merge_threshold=settings.retrieval.auto_merge_threshold,
        use_hybrid=settings.retrieval.use_hybrid,
        hybrid_vector_weight=settings.retrieval.hybrid_vector_weight,
        rrf_k=settings.retrieval.rrf_k,
        reranker=reranker,
        early_exit_matcher=early_exit_matcher,
        faithfulness_evaluator=faithfulness_evaluator,
        review_workflow=review_workflow,
        audit_store=audit_store,
        query_rewriter=query_rewriter,
        query_cache=query_cache,
        bm25_index=bm25_index,
    )
