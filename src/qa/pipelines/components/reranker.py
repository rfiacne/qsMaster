"""
Reranker 精排组件 — 对检索结果进行二次打分排序

使用 cross-encoder 模型对 query 和每个候选文档进行语义匹配评分，
比向量余弦相似度更精准。支持远程 API（兼容 OpenAI 格式）。
"""

from __future__ import annotations

import logging
import time

from haystack import Document

from qa.config.settings import get_settings

logger = logging.getLogger(__name__)


class Reranker:
    """重排序器

    对混合检索结果用 cross-encoder 重新打分排序，
    大幅提升最终结果的精准度，尤其能区分相似文档的细微差异。

    API 格式（兼容 SiliconFlow / Jina / Cohere）:
      POST /v1/rerank
      { "model": "...", "query": "...", "documents": [...], "top_n": N }
    """

    def __init__(
        self,
        model: str = "Qwen/Qwen3-Reranker-4B",
        api_base_url: str = "",
        api_key: str | None = None,
        top_k: int = 5,
        timeout: int = 30,
    ):
        self.model = model
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.top_k = top_k
        self.timeout = timeout

    def rerank(
        self,
        query: str,
        documents: list[Document],
        top_k: int | None = None,
    ) -> list[Document]:
        """对文档列表进行重排序

        Args:
            query: 用户原始查询
            documents: 候选文档列表（from hybrid retrieval）
            top_k: 返回结果数

        Returns:
            按 reranker 分数重新排序的文档列表
        """
        if not documents:
            return []

        k = top_k or self.top_k
        t0 = time.time()

        settings = get_settings()
        # 优先用 rerank 自己的 base_url，依次回退 embedding → llm
        base_url = self.api_base_url or settings.embedding.api_base_url or settings.llm.api_base_url
        api_key = (
            self.api_key
            or settings.rerank.resolved_api_key
            or settings.embedding.resolved_api_key
            or settings.llm.resolved_api_key
            or ""
        )

        doc_texts = [d.content or "" for d in documents]

        try:
            scores = self._call_rerank_api(
                query=query, documents=doc_texts,
                base_url=base_url, api_key=api_key,
            )
        except Exception as e:
            logger.warning(f"Reranker 调用失败: {e}，回退到原始排序")
            return documents[:k]

        # 将分数关联到文档并按分数排序
        import dataclasses

        scored = []
        for doc, score in zip(documents, scores):
            meta = dict(doc.meta or {})
            meta["rerank_score"] = round(score, 4)
            new_doc = dataclasses.replace(doc, score=score, meta=meta)
            scored.append(new_doc)

        scored.sort(key=lambda x: -x.score)

        result = scored[:k]
        elapsed = (time.time() - t0) * 1000

        logger.info(
            f"Reranker 精排完成: {len(documents)} → {len(result)} 文档, "
            f"最高分={result[0].score:.4f}, 耗时={elapsed:.0f}ms"
        )

        return result

    def _call_rerank_api(
        self, query: str, documents: list[str],
        base_url: str, api_key: str = "",
    ) -> list[float]:
        """调用远程 Reranker API

        兼容 SiliconFlow / Jina / Cohere 等格式:
        POST {base_url}/rerank
        """
        import requests

        url = f"{base_url.rstrip('/')}/rerank"

        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(
                url, json=payload, headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            # 兼容不同 API 返回格式
            if "results" in data:
                # 标准格式: {"results": [{"index": 0, "relevance_score": 0.9}, ...]}
                results = data["results"]
                scores = [0.0] * len(documents)
                for r in results:
                    idx = r.get("index")
                    score = r.get("relevance_score") or r.get("score", 0)
                    if idx is not None and idx < len(scores):
                        scores[idx] = score
                return scores
            elif "data" in data:
                # OpenAI 兼容格式: {"data": [{"index": 0, "score": 0.9}, ...]}
                scores = [0.0] * len(documents)
                for item in data["data"]:
                    idx = item.get("index")
                    score = item.get("score", 0)
                    if idx is not None and idx < len(scores):
                        scores[idx] = score
                return scores
            else:
                logger.warning(f"Reranker API 返回格式未知: {list(data.keys())}")
                return [0.0] * len(documents)

        except requests.exceptions.Timeout:
            logger.error("Reranker API 超时")
            raise
        except requests.exceptions.HTTPError as e:
            logger.error(f"Reranker API HTTP 错误: {e}")
            raise
