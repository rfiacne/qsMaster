"""
查询 Pipeline — 用户问题 → 嵌入 → 检索 → 合并 → 生成 → 回答

流程:
  问题 → TextEmbedder → EmbeddingRetriever(chunk_store) →
  AutoMergingRetriever(parent_store) → PromptBuilder → Generator

支持引用溯源、无 LLM 模式（仅检索）、超时/错误处理。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from haystack import Document

from qa.config.settings import get_settings
from qa.stores.turbovec_store import StoreManager
from qa.pipelines.components.hybrid_retriever import HybridRetriever
from qa.pipelines.components.reranker import Reranker

logger = logging.getLogger(__name__)


DEFAULT_RAG_TEMPLATE = """你是一个专业的财务与证券清算助手，职责是基于提供的文档准确回答用户问题。

回答要求：
- 直接回答问题，先给出结论，再补充细节
- 如果问题是"多少钱""总计"等汇总类问题，**先算总和**再列明细
- 只基于文档内容，不编造
- 引用来源时标注文件名即可，格式 [文件名]
- 对金额、日期等关键信息确保准确

检索到的文档片段：
{% for doc in documents %}
---
[{{ doc.meta.get('file_path', 'unknown') }}]
{{ doc.content }}
---
{% endfor %}

用户问题: {{ question }}
"""


@dataclass
class SourceRef:
    """引用来源"""

    document_id: str
    file_name: str
    content: str
    source: str
    category: str
    effective_date: str
    score: float


@dataclass
class QueryResult:
    """问答结果"""

    question: str
    answer: Optional[str] = None
    sources: List[SourceRef] = field(default_factory=list)
    retrieval_time_ms: float = 0.0
    generation_time_ms: float = 0.0
    total_time_ms: float = 0.0
    api_error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.api_error is None and self.answer is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "sources": [
                {
                    "document_id": s.document_id,
                    "file_name": s.file_name,
                    "content": s.content[:200] + "..." if len(s.content) > 200 else s.content,
                    "source": s.source,
                    "category": s.category,
                    "effective_date": s.effective_date,
                    "score": s.score,
                }
                for s in self.sources
            ],
            "retrieval_time_ms": self.retrieval_time_ms,
            "generation_time_ms": self.generation_time_ms,
            "total_time_ms": self.total_time_ms,
            "api_error": self.api_error,
        }


class QueryPipeline:
    """问答 Pipeline

    用法:
        pipeline = QueryPipeline(store_manager, embedder, llm_generator)
        result = pipeline.run("沪深交易所 T+1 清算流程是什么？")
    """

    def __init__(
        self,
        store_manager: StoreManager,
        embedder=None,   # TextEmbedder
        llm_generator=None,  # Generator / OpenAIChatGenerator
        top_k: int = 5,
        auto_merge_threshold: float = 0.5,
        use_hybrid: bool = True,
        hybrid_vector_weight: float = 0.5,
        prompt_template: Optional[str] = None,
        reranker: Optional[Reranker] = None,
    ):
        self.store_manager = store_manager
        self.embedder = embedder
        self.llm_generator = llm_generator
        self.top_k = top_k
        self.auto_merge_threshold = auto_merge_threshold
        self.use_hybrid = use_hybrid
        self.prompt_template = prompt_template or DEFAULT_RAG_TEMPLATE
        self.reranker = reranker
        self.hybrid_retriever = HybridRetriever(
            store_manager=store_manager,
            vector_weight=hybrid_vector_weight,
            top_k=top_k * 2,
        ) if use_hybrid else None

    def run(
        self,
        question: str,
        top_k: Optional[int] = None,
        filters: Optional[Dict] = None,
        no_llm: bool = False,
    ) -> QueryResult:
        """执行问答流程

        Args:
            question: 用户问题
            top_k: 检索文档数（覆盖默认值）
            filters: 元数据过滤条件
            no_llm: True 则仅返回检索结果，不调用 LLM

        Returns:
            QueryResult
        """
        t0 = time.time()
        result = QueryResult(question=question)
        k = top_k or self.top_k

        # 0) 检查知识库是否为空
        if self.store_manager.count_chunks() == 0:
            result.api_error = "EMPTY_INDEX"
            result.total_time_ms = (time.time() - t0) * 1000
            return result

        # 1) 嵌入用户问题
        retrieval_t0 = time.time()
        try:
            query_embedding = self._embed_query(question)
        except Exception as e:
            result.api_error = "EMBEDDING_UNAVAILABLE"
            result.total_time_ms = (time.time() - t0) * 1000
            logger.error(f"嵌入查询失败: {e}")
            return result

        # 2) 检索（混合模式：BM25 + 向量，或纯向量）
        try:
            if self.hybrid_retriever is not None:
                chunk_results = self.hybrid_retriever.retrieve(
                    query_embedding=query_embedding,
                    query_text=question,
                    top_k=k,
                    filters=filters,
                )
            else:
                chunk_results = self.store_manager.retrieve(
                    query_embedding=query_embedding,
                    top_k=k,
                    filters=filters,
                )
        except Exception as e:
            result.api_error = "EMBEDDING_UNAVAILABLE"
            result.total_time_ms = (time.time() - t0) * 1000
            logger.error(f"检索失败: {e}")
            return result

        if not chunk_results:
            # 无检索结果 — 使用空上下文继续
            pass

        # 2.5) Reranker 精排（可选）
        if self.reranker is not None and chunk_results:
            logger.info(f"Reranker 精排: {len(chunk_results)} 个候选文档")
            try:
                chunk_results = self.reranker.rerank(
                    query=question,
                    documents=chunk_results,
                    top_k=self.top_k * 2,
                )
            except Exception as e:
                logger.warning(f"Reranker 失败，使用原始排序: {e}")

        # 3) AutoMerging — 获取父级大块
        context_docs = self._merge_chunks(chunk_results)
        result.retrieval_time_ms = (time.time() - retrieval_t0) * 1000

        # 构建引用来源（使用 LLM 实际看到的上下文文档，与前端展示一致）
        result.sources = self._build_sources(context_docs)
        # 去重：同文件的多个片段合并，前端展示更简洁
        result.sources = self._dedup_sources(result.sources)

        # 4) 仅检索模式
        if no_llm:
            result.total_time_ms = (time.time() - t0) * 1000
            return result

        # 5) LLM 生成
        gen_t0 = time.time()
        try:
            answer = self._generate(question, context_docs)
            result.answer = answer
            result.generation_time_ms = (time.time() - gen_t0) * 1000
        except TimeoutError:
            result.api_error = "API_TIMEOUT"
            result.generation_time_ms = (time.time() - gen_t0) * 1000
            logger.error("LLM 生成超时")
        except Exception as e:
            result.api_error = "LLM_UNAVAILABLE"
            result.generation_time_ms = (time.time() - gen_t0) * 1000
            logger.error(f"LLM 生成失败: {e}")

        result.total_time_ms = (time.time() - t0) * 1000
        return result

    def _embed_query(self, question: str) -> List[float]:
        """将问题转为嵌入向量（自动选择远程 API 或本地模型）"""
        if self.embedder is not None:
            # 使用 Haystack TextEmbedder
            result = self.embedder.run(text=question)
            return result["embedding"]
        else:
            from qa.pipelines.components.embedder import embed_query
            return embed_query(question)

    def _merge_chunks(self, chunks: List[Document]) -> List[Document]:
        """合并检索到的小块 → 获取父级大块

        如果同一 parent 有多个 chunk 命中（超过阈值），
        用父级大块替代多个小块，保证上下文完整性。
        """
        if not chunks:
            return []

        # 按 parent_id 分组
        parent_hits: Dict[str, List[Document]] = {}
        for chunk in chunks:
            pid = chunk.meta.get("parent_id") if chunk.meta else None
            if pid:
                parent_hits.setdefault(pid, []).append(chunk)

        # 超过阈值的 parent 用大块替换
        merged: List[Document] = []
        seen_parents: set = set()

        for chunk in chunks:
            pid = chunk.meta.get("parent_id") if chunk.meta else None
            if pid and pid in parent_hits:
                hits = parent_hits[pid]
                hit_ratio = len(hits) / self.top_k

                if hit_ratio >= self.auto_merge_threshold and pid not in seen_parents:
                    # 获取父级大块
                    parents = self.store_manager.get_parent_docs([pid])
                    if parents:
                        merged.append(parents[0])
                        seen_parents.add(pid)
                        continue

            # 未合并的小块直接加入
            merged.append(chunk)

        return merged

    def _dedup_sources(self, sources: List[SourceRef]) -> List[SourceRef]:
        """按文件名去重合并来源列表

        同一个文件的多个片段合并为一条，保留最高分。
        """
        seen: Dict[str, SourceRef] = {}
        for s in sources:
            key = s.file_name
            if key not in seen or s.score > seen[key].score:
                seen[key] = s
        return list(seen.values())

    def _build_sources(self, chunks: List[Document]) -> List[SourceRef]:
        """从检索结果构建引用来源列表"""
        sources = []
        for chunk in chunks:
            meta = chunk.meta or {}
            sources.append(
                SourceRef(
                    document_id=chunk.id or "",
                    file_name=self._short_name(meta.get("file_path", "unknown")),
                    content=(chunk.content or "")[:300],
                    source=meta.get("source", ""),
                    category=meta.get("category", ""),
                    effective_date=meta.get("effective_date", ""),
                    score=chunk.score or meta.get("score", 0.0),
                )
            )
        return sources

    @staticmethod
    def _short_name(path: str) -> str:
        """从完整路径中提取文件名"""
        if not path or path == "unknown":
            return "unknown"
        # 取最后一个分隔符后的部分
        name = path.replace("\\", "/").split("/")[-1]
        # 如果文件名太长（含 UUID 前缀），截断
        if len(name) > 50:
            # 尝试保留后半段（通常是"机构_类别.pdf"）
            parts = name.split("_", 2)
            if len(parts) >= 3:
                name = "_".join(parts[-2:])
        return name

    def _generate(self, question: str, context_docs: List[Document]) -> str:
        """调用 LLM 生成回答

        使用 OpenAI 兼容 API 直接调用（支持流式和非流式）。
        """
        if self.llm_generator is not None:
            # 使用 Haystack Generator
            result = self.llm_generator.run(
                prompt_builder=self.prompt_template,
                question=question,
                documents=context_docs,
            )
            return result["replies"][0]

        # 直接使用 OpenAI API
        settings = get_settings()
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.llm.resolved_api_key or "sk-placeholder",
            base_url=settings.llm.api_base_url,
            timeout=settings.llm.timeout_seconds,
        )

        # 构建上下文（使用简短文件名）
        context_parts = []
        for doc in context_docs:
            meta = doc.meta or {}
            source = self._short_name(meta.get("file_path", "unknown"))
            context_parts.append(f"[{source}]\n{doc.content}")

        context_text = "\n---\n".join(context_parts)

        if not context_text.strip():
            context_text = "（知识库中未找到相关文档）"

        system_prompt = (
            "你是一个证券清算与技术领域的专业问答助手。请基于以下检索到的文档片段回答用户的问题。\n\n"
            "要求：\n"
            "1. 只基于检索到的文档内容回答，不要编造信息\n"
            "2. 如果文档内容不足以回答问题，明确说明'根据现有知识库内容，无法完整回答此问题'\n"
            "3. 在回答中标注引用来源，格式为 [来源:文件名]\n"
            "4. 对于涉及金额、日期、规则编号的具体信息，确保准确无误"
        )

        resp = client.chat.completions.create(
            model=settings.llm.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"检索到的文档片段：\n{context_text}\n\n用户问题: {question}"},
            ],
            temperature=0.3,
            max_tokens=2048,
        )

        return resp.choices[0].message.content or ""
