"""
混合检索模块 — BM25 关键词检索 + 向量语义检索融合

使用 RRF（Reciprocal Rank Fusion）算法融合两种检索结果，
兼顾证券清算文档中精确匹配（条文编号、日期、金额）和语义匹配。

M6 增强：
- BM25 改为全量持久化索引（GlobalBM25Index），消除每请求 O(n) 重建
- RRF k 可配置（默认 35），提升排序区分度
- 展示分数改为归一化 RRF 分数（FR-004）
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from haystack import Document

from qa.pipelines.components.bm25_index import GlobalBM25Index, _tokenize

# 复用的检索线程池：避免每次查询都创建/销毁 ThreadPoolExecutor 带来的线程抖动。
# 默认 4 个工作线程，可在并发查询时共享，同时限制资源上限。
_RETRIEVAL_EXECUTOR: ThreadPoolExecutor | None = None
_RETRIEVAL_EXECUTOR_LOCK = threading.Lock()


def _get_retrieval_executor() -> ThreadPoolExecutor:
    """惰性创建并复用检索线程池（线程安全）"""
    global _RETRIEVAL_EXECUTOR
    if _RETRIEVAL_EXECUTOR is None:
        with _RETRIEVAL_EXECUTOR_LOCK:
            if _RETRIEVAL_EXECUTOR is None:
                _RETRIEVAL_EXECUTOR = ThreadPoolExecutor(
                    max_workers=4, thread_name_prefix="hybrid_retrieve"
                )
    return _RETRIEVAL_EXECUTOR


logger = logging.getLogger(__name__)


class HybridRetriever:
    """混合检索器

    同时执行 BM25 关键词检索（全量持久化索引）和向量语义检索，
    用 RRF 算法融合排序。

    RRF 公式: score(d) = 1/(k + rank_v(d)) + 1/(k + rank_b(d))
    其中 rank_v 是向量检索排名，rank_b 是 BM25 检索排名。
    """

    def __init__(
        self,
        store_manager,
        vector_weight: float = 0.5,
        rrf_k: int = 35,
        top_k: int = 10,
        pg_retriever=None,
        bm25_index: GlobalBM25Index | None = None,
    ):
        """
        Args:
            store_manager: StoreManager 实例（含 chunk_store）
            vector_weight: 向量检索权重 (0-1)，剩余为 BM25 权重
            rrf_k: RRF 常数，越小排名区分度越大（默认 35，M6 优化值）
            top_k: 最终返回结果数
            pg_retriever: PgFullTextRetriever 实例（可选，替代 BM25）
            bm25_index: 全量 BM25 索引（GlobalBM25Index），
                        若未提供则回退到 PG 或向量-only 检索
        """
        self.store_manager = store_manager
        self.vector_weight = max(0.0, min(1.0, vector_weight))
        self.rrf_k = rrf_k
        self.top_k = top_k
        self.pg_retriever = pg_retriever
        self.bm25_index = bm25_index

    # 保留 _tokenize 供外部测试调用（委托给 bm25_index 的同名函数）
    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return _tokenize(text)

    def retrieve(
        self,
        query_embedding: list[float],
        query_text: str,
        top_k: int | None = None,
        filters: dict | None = None,
    ) -> list[Document]:
        """混合检索

        1. 向量检索（语义匹配）— 从 chunk_store 检索
        2. BM25 检索（关键词精确匹配）— 从全量持久化索引检索
        3. PG 全文检索（可选，优先于 BM25）
        4. RRF 融合排序
        5. 展示分数归一化
        """
        k = top_k or self.top_k
        t0 = time.time()

        # ── 向量检索 + BM25 检索（并行执行，两者完全独立） ──
        use_bm25 = self.bm25_index is not None and self.bm25_index.is_built
        vec_topk = max(k * 3, 30)

        if use_bm25:
            ex = _get_retrieval_executor()
            vec_fut = ex.submit(
                self.store_manager.retrieve,
                query_embedding=query_embedding,
                top_k=vec_topk,
                filters=filters,
            )
            bm25_fut = ex.submit(self.bm25_index.retrieve, query_text, top_k=vec_topk)
            vector_results = vec_fut.result()
            bm25_docs = bm25_fut.result() or []
            logger.info(f"并行检索完成: vector={len(vector_results)}, bm25={len(bm25_docs)}")
        else:
            vector_results = self.store_manager.retrieve(
                query_embedding=query_embedding,
                top_k=vec_topk,
                filters=filters,
            )
            bm25_docs = []

        if not vector_results:
            logger.info("混合检索: 向量检索无结果")
            # 即使向量无结果，仍尝试 BM25 全量检索
            if bm25_docs:
                logger.info(f"混合检索: BM25 补充 {len(bm25_docs)} 结果")
                return bm25_docs[:k]
            return []

        # ── 自适应权重：向量 top score 低时提升 BM25 权重 ──
        effective_vector_weight = self.vector_weight
        if vector_results:
            vec_top_score = max((d.score or 0.0) for d in vector_results[:3])
            if vec_top_score < 0.5:
                effective_vector_weight = self.vector_weight * 0.5
                logger.info(
                    f"自适应权重: vec_top={vec_top_score:.3f} "
                    f"→ vector_weight={effective_vector_weight:.2f}"
                )

        # ── PG 全文检索（可选，优先于 BM25） ──
        pg_text_map: dict[str, float] = {}
        if self.pg_retriever is not None and self.pg_retriever.available:
            try:
                pg_results = self.pg_retriever.search(query_text, top_k=max(k * 3, 30))
                if pg_results:
                    for r in pg_results:
                        content_key = (r.get("content") or "")[:100]
                        if content_key:
                            pg_text_map[content_key] = max(
                                pg_text_map.get(content_key, 0.0),
                                r["score"],
                            )
                    logger.info(f"全文检索(PG): {len(pg_results)} 结果")
            except Exception as e:
                logger.warning(f"PG 检索异常，跳过: {e}")

        # ── 合并向量与 BM25 候选集 ──
        # 以向量结果为基准，构建 id→doc 映射
        all_candidates: dict[str, tuple[int, Document]] = {}
        for i, doc in enumerate(vector_results):
            doc_id = doc.id or f"v_{i}"
            all_candidates[doc_id] = (i, doc)

        # 补充 BM25 结果中不在向量结果中的文档
        bm25_only_indices: list[int] = []
        for bm25_doc in bm25_docs:
            doc_id = bm25_doc.id or ""
            if doc_id and doc_id not in all_candidates:
                idx = len(vector_results) + len(bm25_only_indices)
                all_candidates[doc_id] = (idx, bm25_doc)
                bm25_only_indices.append(idx)

        # ── RRF 融合 ──
        vec_rank: dict[str, int] = {}
        for i, doc in enumerate(vector_results):
            doc_id = doc.id or f"v_{i}"
            vec_rank[doc_id] = i

        # BM25 排名（基于全量 BM25 结果）
        bm25_rank: dict[str, int] = {}
        for rank_idx, bm25_doc in enumerate(bm25_docs):
            doc_id = bm25_doc.id or ""
            if doc_id:
                bm25_rank[doc_id] = rank_idx

        # PG 排名（基于 content 匹配，覆盖 BM25 排名）
        if pg_text_map:
            pg_scores_list: list[float] = []
            for doc_id, (idx, doc) in all_candidates.items():
                content_key = (doc.content or "")[:100]
                if content_key and content_key in pg_text_map:
                    pg_scores_list.append(pg_text_map[content_key])
                else:
                    pg_scores_list.append(0.0)
            if any(s > 0 for s in pg_scores_list):
                pg_sorted = sorted(enumerate(pg_scores_list), key=lambda x: -x[1])
                for rank_idx, (candidate_idx, _) in enumerate(pg_sorted):
                    doc_id = list(all_candidates.keys())[candidate_idx]
                    bm25_rank[doc_id] = rank_idx

        # 计算 RRF 分数
        rrf_scores: list[tuple[str, float]] = []
        for doc_id, (idx, doc) in all_candidates.items():
            v_rank = vec_rank.get(doc_id, k * 10)
            b_rank = bm25_rank.get(doc_id, k * 10)

            v_score = 1.0 / (self.rrf_k + v_rank + 1)
            b_score = 1.0 / (self.rrf_k + b_rank + 1)

            fused = effective_vector_weight * v_score + (1 - effective_vector_weight) * b_score
            rrf_scores.append((doc_id, fused))

        # 按融合分数排序
        rrf_scores.sort(key=lambda x: -x[1])

        # 取 top_k（结果直接用于下方构建）

        # ── 来源类型标注（FR-005） ──
        vec_top_set: set[str] = set()
        for i, d in enumerate(vector_results):
            if i < k:
                doc_id = d.id or f"v_{i}"
                vec_top_set.add(doc_id)

        bm25_top_set: set[str] = set()
        for rank_idx, bm25_doc in enumerate(bm25_docs):
            if rank_idx < k:
                doc_id = bm25_doc.id or ""
                if doc_id:
                    bm25_top_set.add(doc_id)

        # 找最大 RRF 分数用于归一化
        max_rrf = max((s for _, s in rrf_scores[:k]), default=1.0)
        if max_rrf <= 0:
            max_rrf = 1.0

        # 构建结果
        import dataclasses

        results: list[Document] = []
        for rank, (doc_id, rrf_score) in enumerate(rrf_scores[:k]):
            idx, doc = all_candidates[doc_id]
            meta = dict(doc.meta or {})
            meta["hybrid_score"] = round(rrf_score, 4)
            meta["vec_score"] = round(doc.score or 0.0, 4)

            # 来源类型标注
            in_vec = doc_id in vec_top_set
            in_bm25 = doc_id in bm25_top_set
            if in_vec and in_bm25:
                meta["source_type"] = "hybrid"
            elif in_vec:
                meta["source_type"] = "vector"
            elif in_bm25:
                meta["source_type"] = "bm25"
            else:
                meta["source_type"] = "hybrid"

            # FR-004: 展示分数改为归一化 RRF 分数
            display_score = rrf_score / max_rrf
            doc = dataclasses.replace(doc, score=display_score, meta=meta)
            results.append(doc)

        elapsed = (time.time() - t0) * 1000
        logger.info(
            f"混合检索完成: {len(results)} 结果, "
            f"融合权重 vec={self.vector_weight}, "
            f"rrf_k={self.rrf_k}, "
            f"BM25 全量={len(bm25_docs)}, "
            f"耗时 {elapsed:.0f}ms"
        )

        return results
