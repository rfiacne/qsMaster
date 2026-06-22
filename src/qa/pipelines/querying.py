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
from typing import Any

import openai
from haystack import Document

from qa.config.settings import get_settings
from qa.pipelines.components.audit_logger import AuditRecord, AuditStore
from qa.pipelines.components.faithfulness import FaithfulnessEvaluator
from qa.pipelines.components.hybrid_retriever import HybridRetriever
from qa.pipelines.components.query_cache import QueryCache
from qa.pipelines.components.query_rewriter import QueryRewriter
from qa.pipelines.components.reranker import Reranker
from qa.pipelines.components.review_queue import ReviewWorkflow
from qa.pipelines.components.tracing import get_metrics, get_tracer
from qa.stores.turbovec_store import StoreManager

logger = logging.getLogger(__name__)


# ── RAG Prompt ───────────────────────────────────────────

SYSTEM_PROMPT = (
    "你是一个证券清算与技术领域的专业问答助手。请基于以下检索到的文档片段回答用户的问题。\n\n"
    "要求：\n"
    "1. 只基于检索到的文档内容回答，不要编造信息\n"
    "2. 若无任何片段支撑该问题，必须仅回复'根据现有知识库无法回答该问题'，不得拼凑\n"
    "3. 在回答中标注引用来源，格式为 [来源:文件名]\n"
    "4. 对于涉及金额、日期、规则编号的具体信息，确保准确无误"
)

SYSTEM_PROMPT_WITH_HISTORY = (
    "你是一个证券清算与技术领域的专业问答助手。请基于以下检索到的文档片段回答用户的问题。\n\n"
    "要求：\n"
    "1. 只基于检索到的文档内容回答，不要编造信息\n"
    "2. 若无任何片段支撑该问题，必须仅回复'根据现有知识库无法回答该问题'，不得拼凑\n"
    "3. 在回答中标注引用来源，格式为 [来源:文件名]\n"
    "4. 对于涉及金额、日期、规则编号的具体信息，确保准确无误\n"
    "5. 回答时可以利用对话历史中的上下文，但不要重复对话历史中的内容"
)

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
    source_type: str = ""  # "vector" | "bm25" | "hybrid" | ""


@dataclass
class QueryResult:
    """问答结果"""

    question: str
    answer: str | None = None
    sources: list[SourceRef] = field(default_factory=list)
    retrieval_time_ms: float = 0.0
    generation_time_ms: float = 0.0
    total_time_ms: float = 0.0
    api_error: str | None = None
    # 查询改写字段
    rewritten_question: str = ""
    # Early Exit 字段
    from_standard_answer: bool = False
    match_type: str = ""  # "exact" | "fuzzy" | ""
    standard_answer_id: str = ""
    # Faithfulness 校验字段
    faithfulness: dict[str, Any] | None = None

    @property
    def success(self) -> bool:
        return self.api_error is None and self.answer is not None

    def to_dict(self) -> dict[str, Any]:
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
                    "source_type": s.source_type,
                }
                for s in self.sources
            ],
            "retrieval_time_ms": self.retrieval_time_ms,
            "generation_time_ms": self.generation_time_ms,
            "total_time_ms": self.total_time_ms,
            "api_error": self.api_error,
            "from_standard_answer": self.from_standard_answer,
            "match_type": self.match_type,
            "standard_answer_id": self.standard_answer_id,
            "faithfulness": self.faithfulness,
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
        embedder=None,  # TextEmbedder
        llm_generator=None,  # Generator / OpenAIChatGenerator
        top_k: int = 5,
        auto_merge_threshold: float = 0.5,
        use_hybrid: bool = True,
        hybrid_vector_weight: float = 0.5,
        prompt_template: str | None = None,
        reranker: Reranker | None = None,
        early_exit_matcher: Any | None = None,
        faithfulness_evaluator: FaithfulnessEvaluator | None = None,
        review_workflow: ReviewWorkflow | None = None,
        audit_store: AuditStore | None = None,
        query_rewriter: QueryRewriter | None = None,
        query_cache: QueryCache | None = None,
        bm25_index: Any = None,
    ):
        self.store_manager = store_manager
        self.embedder = embedder
        self.llm_generator = llm_generator
        self.top_k = top_k
        self.auto_merge_threshold = auto_merge_threshold
        self.use_hybrid = use_hybrid
        self.prompt_template = prompt_template or DEFAULT_RAG_TEMPLATE
        self.reranker = reranker
        self.early_exit_matcher = early_exit_matcher
        self.faithfulness_evaluator = faithfulness_evaluator
        self.review_workflow = review_workflow
        self.audit_store = audit_store
        self.query_rewriter = query_rewriter
        self.query_cache = query_cache
        self.bm25_index = bm25_index
        self.hybrid_retriever = (
            HybridRetriever(
                store_manager=store_manager,
                vector_weight=hybrid_vector_weight,
                top_k=top_k * 2,
                bm25_index=bm25_index,
            )
            if use_hybrid
            else None
        )

    def run(
        self,
        question: str,
        top_k: int | None = None,
        filters: dict | None = None,
        no_llm: bool = False,
        conversation_history: str = "",
    ) -> QueryResult:
        """执行问答流程

        Args:
            question: 用户问题
            top_k: 检索文档数（覆盖默认值）
            filters: 元数据过滤条件
            no_llm: True 则仅返回检索结果，不调用 LLM
            conversation_history: 多轮对话的历史上下文文本

        Returns:
            QueryResult
        """
        tracer = get_tracer()
        metrics = get_metrics()
        t0 = time.time()
        result = QueryResult(question=question)
        k = top_k or self.top_k

        # 0) 检查知识库是否为空
        if self.store_manager.count_chunks() == 0:
            result.api_error = "EMPTY_INDEX"
            result.total_time_ms = (time.time() - t0) * 1000
            return result

        # 0.5) Early Exit — 标准答案库匹配
        if self.early_exit_matcher is not None and self.early_exit_matcher.is_enabled:
            with tracer.start_span("early_exit", {"question": question[:100]}) as span:
                ee_result = self.early_exit_matcher.match(question)
                span.set_attribute("matched", str(ee_result.matched))
            if ee_result.matched:
                result.answer = ee_result.answer.answer
                result.from_standard_answer = True
                result.match_type = ee_result.match_type
                result.standard_answer_id = ee_result.answer.id
                # 构建标准答案来源引用
                result.sources = [
                    SourceRef(
                        document_id=ee_result.answer.id,
                        file_name="标准答案库",
                        content=(f"Q: {ee_result.answer.question}\nA: {ee_result.answer.answer}")[
                            :300
                        ],
                        source=ee_result.answer.source or "标准答案库",
                        category=ee_result.answer.category,
                        effective_date=ee_result.answer.effective_date or "",
                        score=ee_result.score,
                    )
                ]
                result.retrieval_time_ms = 0.0
                result.generation_time_ms = 0.0
                result.total_time_ms = (time.time() - t0) * 1000
                logger.info(
                    f"Early Exit 命中 (match={ee_result.match_type}, "
                    f"score={ee_result.score:.4f}, "
                    f"耗时={result.total_time_ms:.0f}ms)"
                )
                return result

        # 0.7) 查询缓存 — Early Exit 之后，改写/嵌入之前
        if self.query_cache is not None:
            with tracer.start_span("query_cache_check") as span:
                try:
                    cached_result = self.query_cache.get(
                        question=question,
                        top_k=k,
                        filters=filters,
                    )
                    if cached_result is not None:
                        logger.info(f"查询缓存命中: {question[:60]}")
                        span.set_attribute("hit", "true")
                        return cached_result
                    span.set_attribute("hit", "false")
                except Exception as e:
                    logger.warning(f"查询缓存检查异常（不影响主流程）: {e}")

        # 0.75) 查询改写（术语归一化 + 多意图分解）
        effective_question = question
        if self.query_rewriter is not None:
            with tracer.start_span("query_rewrite") as span:
                try:
                    rewrite_result = self.query_rewriter.rewrite(question)
                    result.rewritten_question = rewrite_result.rewritten
                    span.set_attribute("was_rewritten", str(rewrite_result.was_rewritten))
                    span.set_attribute("method", rewrite_result.rewrite_method)

                    if rewrite_result.sub_questions and len(rewrite_result.sub_questions) > 1:
                        # 多意图：分别嵌入检索后合并
                        logger.info(f"多意图分解: {len(rewrite_result.sub_questions)} 子问题")
                        retrieval_t0 = time.time()
                        all_chunks: list[Document] = []
                        for sq in rewrite_result.sub_questions:
                            try:
                                sq_emb = self._embed_query(sq)
                                sq_results = self._retrieve_chunks(
                                    sq_emb, sq, top_k=k, filters=filters
                                )
                                if sq_results:
                                    all_chunks.extend(sq_results)
                            except Exception as sq_e:
                                logger.warning(f'子问题检索失败 "{sq}": {sq_e}')

                        # 去重（按文档 id）
                        seen_ids: set = set()
                        chunk_results = []
                        for doc in all_chunks:
                            doc_id = doc.id or ""
                            if doc_id not in seen_ids:
                                seen_ids.add(doc_id)
                                chunk_results.append(doc)

                        logger.info(f"多意图合并后: {len(all_chunks)} → {len(chunk_results)} 去重")
                        # 直接跳转到 Reranker（跳过下方单次嵌入+检索）
                        skip_embed_and_retrieve = True
                    else:
                        # 单一意图：用改写后问题
                        effective_question = rewrite_result.rewritten
                        skip_embed_and_retrieve = False
                except Exception as e:
                    logger.warning(f"查询改写异常（回退原始问题）: {e}")
                    skip_embed_and_retrieve = False
        else:
            skip_embed_and_retrieve = False

        if not skip_embed_and_retrieve:
            # 1) 嵌入用户问题（使用改写后或原始问题）
            retrieval_t0 = time.time()
            with tracer.start_span("embedding") as span:
                try:
                    query_embedding = self._embed_query(effective_question)
                    span.set_attribute("dim", len(query_embedding) if query_embedding else 0)
                except Exception as e:
                    result.api_error = "EMBEDDING_UNAVAILABLE"
                    result.total_time_ms = (time.time() - t0) * 1000
                    span.set_status(f"error: {e}")
                    logger.error(f"嵌入查询失败: {e}")
                    return result

            # 2) 检索（混合模式：BM25 + 向量，或纯向量）
            with tracer.start_span("retrieval") as span:
                try:
                    if self.hybrid_retriever is not None:
                        chunk_results = self.hybrid_retriever.retrieve(
                            query_embedding=query_embedding,
                            query_text=effective_question,
                            top_k=k,
                            filters=filters,
                        )
                    else:
                        chunk_results = self.store_manager.retrieve(
                            query_embedding=query_embedding,
                            top_k=k,
                            filters=filters,
                        )
                    span.set_attribute("result_count", len(chunk_results) if chunk_results else 0)
                except Exception as e:
                    result.api_error = "RETRIEVAL_UNAVAILABLE"
                    result.total_time_ms = (time.time() - t0) * 1000
                    span.set_status(f"error: {e}")
                    logger.error(f"检索失败: {e}")
                    return result

        # 2.5) Reranker 精排（可选）
        if self.reranker is not None and chunk_results:
            logger.info(f"Reranker 精排: {len(chunk_results)} 个候选文档")
            try:
                chunk_results = self.reranker.rerank(
                    query=effective_question,
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
        with tracer.start_span("llm_generation", {"question": question[:100]}) as span:
            try:
                answer = self._generate(question, context_docs, conversation_history)
                result.answer = answer
                result.generation_time_ms = (time.time() - gen_t0) * 1000
                span.set_attribute("tokens", len(answer) if answer else 0)
            except (openai.APITimeoutError, TimeoutError):
                result.api_error = "API_TIMEOUT"
                result.generation_time_ms = (time.time() - gen_t0) * 1000
                span.set_status("timeout")
                logger.error("LLM 生成超时")
            except Exception as e:
                result.api_error = "LLM_UNAVAILABLE"
                result.generation_time_ms = (time.time() - gen_t0) * 1000
                span.set_status(f"error: {e}")
                logger.error(f"LLM 生成失败: {e}")

        # 6) Faithfulness 校验 — 检查回答是否有检索支撑
        _original_answer = result.answer  # 保存原始回答，供降级/审核使用
        _should_skip_faithfulness = (
            result.answer is None
            or self.faithfulness_evaluator is None
            or result.from_standard_answer
            or self._is_low_risk_context(chunk_results, self.reranker is not None)
        )
        if not _should_skip_faithfulness:
            with tracer.start_span("faithfulness_check") as span:
                try:
                    report = self.faithfulness_evaluator.evaluate(
                        question=question,
                        answer=result.answer,
                        context_docs=context_docs,
                    )
                    result.faithfulness = report.to_dict()
                    # 保存原始回答用于审核入队（避免降级提示文本替代原始回答）
                    result.faithfulness["original_answer"] = _original_answer

                    if report.degraded:
                        logger.warning(
                            "Faithfulness 校验不通过，回答降级: "
                            f"支撑比例 {report.score:.2f}"
                            f" < 阈值 {self.faithfulness_evaluator.threshold}"
                        )
                        result.answer = (
                            "根据当前知识库无法确认该问题的答案。\n\n"
                            "以下为 LLM 原始生成内容（未经校验），请注意甄别：\n\n"
                            f"{result.answer}"
                        )
                        metrics.record_faithfulness(passed=False)
                    else:
                        metrics.record_faithfulness(passed=True)

                    span.set_attribute("result", report.result.value if report else "unknown")
                    span.set_attribute("score", report.score if report else 0.0)

                except Exception as e:
                    span.set_status(f"error: {e}")
                    logger.error(f"Faithfulness 校验异常（不影响回答输出）: {e}")

        # 7) 审核队列 — Faithfulness FAIL 自动入队
        if (
            result.faithfulness
            and result.faithfulness.get("result") == "fail"
            and self.review_workflow is not None
        ):
            try:
                self.review_workflow.add_item(
                    question=question,
                    answer=result.faithfulness.get("original_answer", _original_answer or ""),
                    sources=[
                        s.to_dict() if hasattr(s, "to_dict") else vars(s) for s in result.sources
                    ],
                    faithfulness_score=result.faithfulness.get("score", 0.0),
                    faithfulness_result="fail",
                    priority="high",
                )
                logger.info("问答已自动加入审核队列")
            except Exception as e:
                logger.error(f"审核队列入队失败: {e}")

        # 8) 审计日志 — 每笔问答记录
        if self.audit_store is not None:
            try:
                record = AuditRecord(
                    question=question,
                    answer=result.answer or "",
                    retrieval_time_ms=result.retrieval_time_ms,
                    generation_time_ms=result.generation_time_ms,
                    total_time_ms=result.total_time_ms,
                    from_standard_answer=result.from_standard_answer,
                    match_type=result.match_type,
                    faithfulness_result=(
                        result.faithfulness.get("result", "") if result.faithfulness else ""
                    ),
                    faithfulness_score=(
                        result.faithfulness.get("score", 0.0) if result.faithfulness else 0.0
                    ),
                    sources=[
                        {"file_name": s.file_name, "score": s.score, "source_type": s.source_type}
                        for s in result.sources
                    ],
                    api_error=result.api_error,
                )
                self.audit_store.append(record)
            except Exception as e:
                logger.error(f"审计日志记录失败: {e}")

        result.total_time_ms = (time.time() - t0) * 1000

        # 记录 Metrics
        try:
            metrics.record_request(
                latency_ms=result.total_time_ms,
                early_exit=result.from_standard_answer,
            )
        except Exception:
            pass

        # 写入查询缓存（非错误、非 Early Exit 的结果）
        if self.query_cache is not None and result.success and not result.from_standard_answer:
            try:
                self.query_cache.put(
                    question=question,
                    top_k=k,
                    result=result,
                    filters=filters,
                )
                logger.debug(f"查询缓存写入完成: {question[:50]}")
            except Exception as e:
                logger.warning(f"查询缓存写入异常: {e}")

        return result

    def run_stream(
        self, question: str, top_k: int | None = None, filters: dict | None = None
    ) -> Any:
        """流式问答 — 逐 token yield LLM 响应

        与 run() 共享检索和 Early Exit 逻辑，仅 LLM 生成阶段使用流式。

        Yields:
            dict: {"type": "meta" | "token" | "sources" | "done" | "error", ...}
        """
        t0 = time.time()
        k = top_k or self.top_k

        # 0) 空索引检查
        if self.store_manager.count_chunks() == 0:
            yield {
                "type": "error",
                "code": "EMPTY_INDEX",
                "message": "知识库尚未建立，请先导入文档",
            }
            return

        # 0.5) Early Exit
        if self.early_exit_matcher is not None and self.early_exit_matcher.is_enabled:
            ee_result = self.early_exit_matcher.match(question)
            if ee_result.matched:
                sources = [
                    {
                        "file_name": "标准答案库",
                        "content": (
                            f"Q: {ee_result.answer.question}\nA: {ee_result.answer.answer}"
                        )[:300],
                        "score": ee_result.score,
                        "source": ee_result.answer.source or "标准答案库",
                        "source_type": "",
                    }
                ]
                yield {
                    "type": "meta",
                    "from_standard_answer": True,
                    "match_type": ee_result.match_type,
                }
                yield {"type": "token", "text": ee_result.answer.answer}
                yield {"type": "sources", "sources": sources}
                yield {"type": "done", "total_time_ms": (time.time() - t0) * 1000}
                return

        # 0.75) 查询改写（流式模式仅做术语归一化，不做多意图分解）
        effective_question = question
        if self.query_rewriter is not None:
            try:
                rewrite_result = self.query_rewriter.rewrite(question)
                # 流式模式，跳过多意图分解，使用改写后问题（含术语归一化）
                effective_question = rewrite_result.rewritten
            except Exception as e:
                logger.warning(f"查询改写异常（流式回退原始问题）: {e}")

        # 1) 嵌入
        try:
            query_embedding = self._embed_query(effective_question)
        except Exception as e:
            yield {"type": "error", "code": "EMBEDDING_UNAVAILABLE", "message": str(e)}
            return

        yield {"type": "meta", "from_standard_answer": False}

        # 2) 检索
        retrieval_t0 = time.time()
        try:
            if self.hybrid_retriever is not None:
                chunk_results = self.hybrid_retriever.retrieve(
                    query_embedding=query_embedding,
                    query_text=effective_question,
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
            yield {"type": "error", "code": "RETRIEVAL_UNAVAILABLE", "message": str(e)}
            return

        if self.reranker is not None and chunk_results:
            try:
                chunk_results = self.reranker.rerank(
                    query=effective_question,
                    documents=chunk_results,
                    top_k=self.top_k * 2,
                )
            except Exception as e:
                logger.warning(f"Reranker 失败，使用原始排序: {e}")

        context_docs = self._merge_chunks(chunk_results)
        sources = self._build_sources(context_docs)
        sources_dedup = self._dedup_sources(sources)
        retrieval_time = (time.time() - retrieval_t0) * 1000

        # 3) 流式 LLM 生成
        gen_t0 = time.time()
        full_answer = ""
        try:
            for chunk in self._generate_stream(question, context_docs):
                if chunk:
                    full_answer += chunk
                    yield {"type": "token", "text": chunk}
        except (openai.APITimeoutError, TimeoutError):
            yield {"type": "error", "code": "API_TIMEOUT", "message": "请求超时"}
            return
        except Exception as e:
            yield {"type": "error", "code": "LLM_UNAVAILABLE", "message": str(e)}
            return

        generation_time = (time.time() - gen_t0) * 1000

        # 4) 来源
        yield {
            "type": "sources",
            "sources": [
                {
                    "file_name": s.file_name,
                    "content": s.content[:200],
                    "score": s.score,
                    "source_type": s.source_type,
                }
                for s in sources_dedup
            ],
        }

        # 5) Faithfulness（非阻塞，只记录）
        if self.faithfulness_evaluator is not None:
            try:
                report = self.faithfulness_evaluator.evaluate(
                    question=question,
                    answer=full_answer,
                    context_docs=context_docs,
                )
                yield {
                    "type": "faithfulness",
                    "result": report.result.value,
                    "score": report.score,
                    "summary": report.summary,
                }
                if report.degraded and report.unsupported_claims > 0:
                    yield {"type": "degraded_warning", "message": "部分回答无检索支撑，请注意甄别"}
            except Exception:
                pass

        # 6) 审计日志
        if self.audit_store is not None:
            try:
                record = AuditRecord(
                    question=question,
                    answer=full_answer,
                    retrieval_time_ms=retrieval_time,
                    generation_time_ms=generation_time,
                    total_time_ms=(time.time() - t0) * 1000,
                    sources=[
                        {
                            "file_name": s.file_name,
                            "score": s.score,
                            "source_type": s.source_type,
                        }
                        for s in sources_dedup
                    ],
                )
                self.audit_store.append(record)
            except Exception:
                pass

        yield {"type": "done", "total_time_ms": (time.time() - t0) * 1000}

    def _generate_stream(self, question: str, context_docs: list[Document]) -> Any:
        """流式 LLM 生成，逐个 token yield"""
        settings = get_settings()
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.llm.resolved_api_key or "sk-placeholder",
            base_url=settings.llm.api_base_url,
            timeout=settings.llm.timeout_seconds,
        )

        context_parts = []
        for doc in context_docs:
            meta = doc.meta or {}
            source = self._short_name(meta.get("file_path", "unknown"))
            context_parts.append(f"[{source}]\n{doc.content}")
        context_text = (
            "\n---\n".join(context_parts) if context_parts else "（知识库中未找到相关文档）"
        )

        stream = client.chat.completions.create(
            model=settings.llm.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (f"检索到的文档片段：\n{context_text}\n\n用户问题: {question}"),
                },
            ],
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
            stream=True,
        )

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def _embed_query(self, question: str) -> list[float]:
        """将问题转为嵌入向量（自动选择远程 API 或本地模型）"""
        if self.embedder is not None:
            # 使用 Haystack TextEmbedder
            result = self.embedder.run(text=question)
            return result["embedding"]
        else:
            from qa.pipelines.components.embedder import embed_query

            return embed_query(question)

    def _retrieve_chunks(
        self,
        query_embedding: list[float],
        query_text: str,
        top_k: int,
        filters: dict | None = None,
    ) -> list[Document]:
        """嵌入向量检索（混合或纯向量）

        供多意图分解的子问题分别检索使用。
        """
        if self.hybrid_retriever is not None:
            return self.hybrid_retriever.retrieve(
                query_embedding=query_embedding,
                query_text=query_text,
                top_k=top_k,
                filters=filters,
            )
        return self.store_manager.retrieve(
            query_embedding=query_embedding,
            top_k=top_k,
            filters=filters,
        )

    @staticmethod
    def _is_low_risk_context(
        chunk_results: list[Document], reranker_ran: bool = False
    ) -> bool:
        """判断是否为低风险场景（可跳过 Faithfulness 校验）

        低风险条件：Reranker 已执行且最高 rerank 分数 ≥ 0.95，
        表示检索结果高度相关、幻觉风险低。

        仅在 Reranker 执行时判定：Reranker 输出的是 0-1 区间的绝对相关性
        分数，0.95 阈值有明确含义。未启用 Reranker 时 chunk_results 的
        score 是 RRF 归一化展示分数（最高值恒为 1.0），不反映绝对相关性，
        不能据此跳过校验，否则会导致所有混合检索结果都被误判为低风险。
        标准答案场景由 Early Exit 提前处理。
        """
        if not chunk_results:
            return True
        if not reranker_ran:
            return False
        max_score = max((d.score or 0.0) for d in chunk_results)
        return max_score >= 0.95

    def _merge_chunks(self, chunks: list[Document]) -> list[Document]:
        """合并检索到的小块 → 获取父级大块

        如果同一 parent 有多个 chunk 命中（超过阈值），
        用父级大块替代多个小块，保证上下文完整性。
        """
        if not chunks:
            return []

        # 按 parent_id 分组
        parent_hits: dict[str, list[Document]] = {}
        for chunk in chunks:
            pid = chunk.meta.get("parent_id") if chunk.meta else None
            if pid:
                parent_hits.setdefault(pid, []).append(chunk)

        # 超过阈值的 parent 用大块替换
        merged: list[Document] = []
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

    def _dedup_sources(self, sources: list[SourceRef]) -> list[SourceRef]:
        """按文件名去重合并来源列表

        同一个文件的多个片段合并为一条，保留最高分。
        """
        seen: dict[str, SourceRef] = {}
        for s in sources:
            key = s.file_name
            if key not in seen or s.score > seen[key].score:
                seen[key] = s
        return list(seen.values())

    def _build_sources(self, chunks: list[Document]) -> list[SourceRef]:
        """从检索结果构建引用来源列表"""
        sources = []
        for chunk in chunks:
            meta = chunk.meta or {}
            source_type = meta.get("source_type", "")
            if source_type not in ("vector", "bm25", "hybrid"):
                source_type = ""
            sources.append(
                SourceRef(
                    document_id=chunk.id or "",
                    file_name=self._short_name(meta.get("file_path", "unknown")),
                    content=(chunk.content or "")[:300],
                    source=meta.get("source", ""),
                    category=meta.get("category", ""),
                    effective_date=meta.get("effective_date", ""),
                    score=chunk.score or meta.get("score", 0.0),
                    source_type=source_type,
                )
            )
        return sources

    @staticmethod
    def _short_name(path: str) -> str:
        """从完整路径中提取文件名"""
        from qa.utils import short_name

        return short_name(path)

    def _generate(
        self, question: str, context_docs: list[Document], conversation_history: str = ""
    ) -> str:
        """调用 LLM 生成回答

        使用 OpenAI 兼容 API 直接调用（支持流式和非流式）。

        Args:
            question: 用户问题
            context_docs: 检索到的文档片段
            conversation_history: 多轮对话历史上下文文本
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

        history_section = ""
        if conversation_history:
            history_section = f"\n\n=== 对话历史 ===\n{conversation_history}"

        system_prompt = SYSTEM_PROMPT_WITH_HISTORY.replace(
            "\n\n用户问题:",
            f"{history_section}\n\n用户问题:"
        ) if conversation_history else SYSTEM_PROMPT

        resp = client.chat.completions.create(
            model=settings.llm.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (f"检索到的文档片段：\n{context_text}\n\n用户问题: {question}"),
                },
            ],
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
        )

        return resp.choices[0].message.content or ""
