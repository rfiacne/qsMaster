"""
混合检索模块 — BM25 关键词检索 + 向量语义检索融合

使用 RRF（Reciprocal Rank Fusion）算法融合两种检索结果，
兼顾证券清算文档中精确匹配（条文编号、日期、金额）和语义匹配。
"""

from __future__ import annotations

import logging
import time

from haystack import Document

logger = logging.getLogger(__name__)


class HybridRetriever:
    """混合检索器

    同时执行 BM25 关键词检索和向量语义检索，
    用 RRF 算法融合排序。

    RRF 公式: score(d) = 1/(k + rank_v(d)) + 1/(k + rank_b(d))
    其中 rank_v 是向量检索排名，rank_b 是 BM25 检索排名。
    """

    def __init__(
        self,
        store_manager,
        vector_weight: float = 0.5,
        rrf_k: int = 60,
        top_k: int = 10,
        pg_retriever=None,
    ):
        """
        Args:
            store_manager: StoreManager 实例（含 chunk_store）
            vector_weight: 向量检索权重 (0-1)，剩余为 BM25 权重
            rrf_k: RRF 常数，越大排名越平滑
            top_k: 最终返回结果数
            pg_retriever: PgFullTextRetriever 实例（可选，替代 BM25）
        """
        self.store_manager = store_manager
        self.vector_weight = max(0.0, min(1.0, vector_weight))
        self.rrf_k = rrf_k
        self.top_k = top_k
        self.pg_retriever = pg_retriever
        self._bm25_index = None
        self._bm25_docs: list[str] = []

    def _build_bm25_index(self, docs: list[Document]) -> None:
        """构建 BM25 索引（按需，只构建一次）"""
        if self._bm25_index is not None:
            return

        from rank_bm25 import BM25Okapi

        tokenized = [self._tokenize(d.content or "") for d in docs]
        self._bm25_index = BM25Okapi(tokenized)
        self._bm25_docs = [d.content or "" for d in docs]
        logger.info(f"BM25 索引构建完成: {len(docs)} 文档")

    def _tokenize(self, text: str) -> list[str]:
        """分词（中文按字/词切分，英文按空格）"""
        import re
        # 中文按字符切分，英文按单词
        tokens = []
        for part in re.split(r"(\s+)", text):
            if not part.strip():
                continue
            # 中文部分：逐字符
            if any("\u4e00" <= c <= "\u9fff" for c in part):
                tokens.extend(list(part.strip()))
            else:
                # 英文/数字：按空格和标点
                tokens.extend(re.findall(r"[a-zA-Z0-9]+", part.lower()))
        return tokens

    def retrieve(
        self,
        query_embedding: list[float],
        query_text: str,
        top_k: int | None = None,
        filters: dict | None = None,
    ) -> list[Document]:
        """混合检索

        1. 向量检索（语义匹配）
        2. BM25 检索（关键词精确匹配）
        3. RRF 融合排序
        """
        k = top_k or self.top_k
        t0 = time.time()

        # ── 向量检索 ──
        vector_results = self.store_manager.retrieve(
            query_embedding=query_embedding,
            top_k=max(k * 3, 30),  # 多取一些给 RRF 融合
            filters=filters,
        )

        if not vector_results:
            logger.info("混合检索: 向量检索无结果")
            return []

        # ── 全文检索（PG 优先，BM25 降级） ──
        bm25_scores: list[float] = []
        pg_results = None

        if self.pg_retriever is not None and self.pg_retriever.available:
            pg_results = self.pg_retriever.search(query_text, top_k=max(k * 3, 30))
            if pg_results is not None:
                # 构建 PG 分数映射：优先按 content 精确匹配对齐，
                # 因为 PG 的 id 与 turbovec Document.id 可能不同源
                pg_text_scores: list[float] = [0.0] * len(vector_results)
                pg_text_map: dict[str, float] = {}
                for r in pg_results:
                    # 使用 content 前 100 字符作为匹配键（避免全文比较）
                    content_key = (r.get("content") or "")[:100]
                    if content_key:
                        pg_text_map[content_key] = max(
                            pg_text_map.get(content_key, 0.0), r["score"]
                        )
                for i, doc in enumerate(vector_results):
                    content_key = (doc.content or "")[:100]
                    if content_key and content_key in pg_text_map:
                        pg_text_scores[i] = pg_text_map[content_key]
                bm25_scores = pg_text_scores
                pg_match_count = sum(1 for s in bm25_scores if s > 0)
                logger.info(f"全文检索(PG): {len(pg_results)} 结果, {pg_match_count} 匹配")
            else:
                logger.info("PG 不可用，降级到 BM25")

        if not bm25_scores:
            # 降级到 BM25
            self._build_bm25_index(vector_results)
            if self._bm25_index:
                query_tokens = self._tokenize(query_text)
                if query_tokens:
                    raw_scores = self._bm25_index.get_scores(query_tokens)
                    bm25_scores = (
                        raw_scores.tolist()
                        if hasattr(raw_scores, 'tolist')
                        else list(raw_scores)
                    )

        # ── RRF 融合 ──
        # 向量排名
        vec_rank = {d.id or f"_{i}": i for i, d in enumerate(vector_results)}

        # BM25 排名（按 BM25 分数排序）
        bm25_rank: dict[str, int] = {}
        if bm25_scores:
            bm25_sorted = sorted(
                enumerate(bm25_scores), key=lambda x: -x[1]
            )
            for rank_idx, (doc_idx, _score) in enumerate(bm25_sorted):
                doc_id = vector_results[doc_idx].id or f"_{doc_idx}"
                bm25_rank[doc_id] = rank_idx

        # 计算 RRF 分数
        rrf_scores: list[tuple[int, float]] = []
        for i, doc in enumerate(vector_results):
            doc_id = doc.id or f"_{i}"
            v_rank = vec_rank.get(doc_id, k * 10)
            b_rank = bm25_rank.get(doc_id, k * 10)

            v_score = 1.0 / (self.rrf_k + v_rank + 1)
            b_score = 1.0 / (self.rrf_k + b_rank + 1)

            # 加权融合
            fused = self.vector_weight * v_score + (1 - self.vector_weight) * b_score
            rrf_scores.append((i, fused))

        # 按融合分数排序
        rrf_scores.sort(key=lambda x: -x[1])

        # 取 top_k
        top_indices = [idx for idx, _score in rrf_scores[:k]]

        # ── 来源类型标注（FR-005） ──
        # 判断每个文档在纯向量检索和纯 BM25 检索中是否在各自 top_k 内
        vec_top_set: set = set()
        for i, d in enumerate(vector_results):
            if i < k:
                vec_top_set.add(d.id)

        bm25_top_set: set = set()
        if bm25_scores:
            bm25_sorted_idx = sorted(
                range(len(bm25_scores)), key=lambda i: -bm25_scores[i]
            )
            for rank_idx, doc_idx in enumerate(bm25_sorted_idx):
                if rank_idx < k:
                    doc_id = vector_results[doc_idx].id or f"_{doc_idx}"
                    bm25_top_set.add(doc_id)

        # 构建结果：RRF 分数仅用于排序，显示用向量余弦相似度
        import dataclasses
        results = []
        for rank, idx in enumerate(top_indices):
            doc = vector_results[idx]
            doc_id = doc.id or f"_{idx}"
            vec_score = doc.score or 0.0
            meta = dict(doc.meta or {})
            meta["hybrid_score"] = round(rrf_scores[rank][1], 4)
            meta["vec_score"] = round(vec_score, 4)
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
                meta["source_type"] = "hybrid"  # RRF 融合到 top_k 也算融合
            # 用向量余弦相似度作为展示分数（0~1，用户可理解），RRF 只用于排名
            display_score = vec_score
            doc = dataclasses.replace(doc, score=display_score, meta=meta)
            results.append(doc)

        elapsed = (time.time() - t0) * 1000
        logger.info(
            f"混合检索完成: {len(results)} 结果, "
            f"融合权重 vec={self.vector_weight}, "
            f"耗时 {elapsed:.0f}ms"
        )

        return results
