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
from typing import Any

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from qa.config.settings import get_settings
from qa.pipelines.indexing import IndexingPipeline, validate_meta
from qa.pipelines.querying import QueryPipeline
from qa.stores.turbovec_store import create_store_manager

logger = logging.getLogger(__name__)

app = FastAPI(title="Securities QA Agent API", version="0.1.0")

# CORS — 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── 请求/响应模型 ─────────────────────────────────────


class AskRequest(BaseModel):
    question: str
    top_k: int = 5
    filters: dict[str, Any] | None = None
    no_llm: bool = False


class SearchRequest(BaseModel):
    query: str
    top_k: int = 20


# ─── 延迟初始化（首次请求时加载） ─────────────────────

import threading

_store = None
_query_pipeline = None
_index_pipeline = None
_bm25_index = None
_lock = threading.Lock()


def get_store():
    global _store
    if _store is None:
        with _lock:
            if _store is None:  # double-check
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
    global _query_pipeline
    if _query_pipeline is None:
        with _lock:
            if _query_pipeline is None:
                from qa.pipelines.factory import build_query_pipeline

                _query_pipeline = build_query_pipeline(get_settings(), get_store())
    return _query_pipeline


def get_index_pipeline():
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
                )
    return _index_pipeline


# ─── Startup 预热 ─────────────────────────────────────


@app.on_event("startup")
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


# ─── API 路由 ─────────────────────────────────────────


@app.get("/api/v1/qa/status")
async def status():
    """知识库状态"""
    store = get_store()
    s = store.get_status()
    return {
        "document_count": s.document_count,
        "chunk_count": s.chunk_count,
        "index_size_bytes": s.index_size_bytes,
        "last_updated": s.last_updated,
        "bit_width": s.bit_width,
        "dim": s.dim,
        "persist_path": s.persist_path,
    }


@app.post("/api/v1/qa/ask")
async def ask(req: AskRequest):
    """问答"""
    try:
        pipeline = get_query_pipeline()
        result = pipeline.run(
            question=req.question,
            top_k=req.top_k,
            filters=req.filters,
            no_llm=req.no_llm,
        )
        return result.to_dict()
    except Exception as e:
        logger.error(f"问答请求失败: {e}", exc_info=True)
        return {"error": str(e), "code": "INTERNAL"}


@app.post("/api/v1/qa/ask/stream")
async def ask_stream(req: AskRequest):
    """流式问答 — SSE 响应"""
    from fastapi.responses import StreamingResponse

    pipeline = get_query_pipeline()

    async def event_generator():
        try:
            for event in pipeline.run_stream(
                question=req.question,
                top_k=req.top_k,
                filters=req.filters,
            ):
                import json

                event_type = event.get("type", "data")
                data = json.dumps(event, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data}\n\n"

                if event_type == "error" or event_type == "done":
                    break
        except Exception as e:
            logger.error(f"流式问答异常: {e}", exc_info=True)
            yield f"event: error\ndata: {{\"error\": \"{str(e)}\"}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/qa/sessions")
async def list_sessions(limit: int = 20):
    """列出最近会话"""
    from qa.pipelines.components.session_store import SessionStore

    store = SessionStore()
    store.load()
    sessions = store.list_recent(limit=limit)
    return {
        "sessions": [
            {
                "id": s.id,
                "title": s.title or "(新会话)",
                "turns": len(s.turns),
                "updated_at": s.updated_at,
                "created_at": s.created_at,
            }
            for s in sessions
        ]
    }


@app.delete("/api/v1/qa/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    from qa.pipelines.components.session_store import SessionStore

    store = SessionStore()
    store.load()
    if store.delete(session_id):
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="会话不存在")


@app.post("/api/v1/qa/search")
async def search(req: SearchRequest):
    """知识库检索（仅检索，不生成回答）"""
    pipeline = get_query_pipeline()
    result = pipeline.run(
        question=req.query,
        top_k=req.top_k,
        no_llm=True,
    )
    # 从配置读取最低分数阈值
    srv_settings = get_settings()
    min_score = srv_settings.retrieval.min_score

    # 过滤低分结果（RRF 融合分数通常较低，用配置值或自适应取 top 的 30%）
    scores_raw = [s.score for s in result.sources]
    if scores_raw:
        score_max = max(scores_raw)
        # 自适应阈值：取最高分的 10% 为底线
        adaptive_min = max(min_score, score_max * 0.1)
        filtered = [s for s in result.sources if s.score >= adaptive_min]
    else:
        filtered = []
        score_max = 1.0

    # 按文件聚合：同一个文件的多个片段合并为一条结果
    from collections import defaultdict

    file_groups: dict[str, list] = defaultdict(list)
    for s in filtered:
        file_groups[s.file_name].append(s)

    # 每个文件取最高分片段作为代表，统计 chunk 数
    from pathlib import Path as _Path

    grouped = []
    for file_name, chunks in file_groups.items():
        best = max(chunks, key=lambda x: x.score)
        # 只保留文件名
        short_name = _Path(file_name).name
        grouped.append(
            {
                "file_name": short_name,
                "content": best.content,
                "source": best.source,
                "category": best.category,
                "effective_date": best.effective_date,
                "score": round(best.score, 4),
                "chunks": len(chunks),  # 该文件命中的片段数
            }
        )

    # 按分数排序
    grouped.sort(key=lambda x: -x["score"])

    # 分数归一化
    scores = [g["score"] for g in grouped]
    score_max_f = max(scores) if scores else 1.0
    score_min_f = min(scores) if scores else 0.0
    score_range = max(score_max_f - score_min_f, 0.001)

    for g in grouped:
        if score_max_f == score_min_f:
            g["score_pct"] = 100.0  # 所有结果同分时都显示 100%
        else:
            g["score_pct"] = round((g["score"] - score_min_f) / score_range * 100, 1)

    return {
        "query": req.query,
        "min_score": min_score,
        "files": len(grouped),
        "total_chunks": len(filtered),
        "score_range": {"min": round(score_min_f, 4), "max": round(score_max_f, 4)},
        "results": grouped,
        "time_ms": result.retrieval_time_ms,
    }


@app.post("/api/v1/qa/upload")
async def upload(
    files: list[UploadFile],
    source: str = Form(default=""),
    category: str = Form(default=""),
    effective_date: str = Form(default=""),
    meta: str = Form(default=""),  # 兼容前端 JSON 字符串格式
):
    """上传并索引文档"""
    # 如果前端传了 meta JSON 字符串，从中解析字段
    if meta:
        try:
            meta_dict = json.loads(meta)
            source = source or meta_dict.get("source", "")
            category = category or meta_dict.get("category", "")
            effective_date = effective_date or meta_dict.get("effective_date", "")
        except json.JSONDecodeError:
            pass

    meta_dict = {"source": source, "category": category, "effective_date": effective_date}

    missing = validate_meta(meta_dict)
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少必需元数据: {', '.join(missing)}")

    # 保存上传文件到临时目录
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="qa_upload_"))
    file_paths = []

    import asyncio

    try:
        for f in files:
            dest = tmp_dir / f.filename
            content = await f.read()
            dest.write_bytes(content)
            file_paths.append(str(dest))

        pipeline = get_index_pipeline()
        # 在线程池中运行同步阻塞的 pipeline，避免卡死 event loop
        srv_settings = get_settings()
        pipe_timeout = max(60, srv_settings.embedding.timeout_seconds + 30)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(pipeline.run, file_paths, meta_dict),
                timeout=pipe_timeout,
            )
        except TimeoutError:
            raise RuntimeError(f"索引超时 ({pipe_timeout}s)。查看服务端日志确认卡在哪一步。")

        # 显式持久化（StoreWriter 不再自动 save）
        store = get_store()
        await asyncio.to_thread(store.save)

        # 索引已变更：清空 pipeline/BM25 单例，查询缓存按版本自动失效
        reset_runtime_singletons()

        return {
            "files_count": result.files_count,
            "documents_written": result.documents_written,
            "documents_skipped": result.documents_skipped,
            "segments": result.chunk_count,  # 检索用片段数
            "parents": result.parent_count,  # 上下文用大块数
            "errors": result.errors,
            "time_ms": result.total_time_ms,
        }
    finally:
        # 清理临时文件
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


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


# ─── 标准答案库 API ─────────────────────────────────


def _get_answer_store():
    """获取标准答案库存储"""
    from qa.pipelines.components.early_exit import StandardAnswer, StandardAnswerStore

    settings = get_settings()
    store = StandardAnswerStore(store_path=settings.early_exit.store_path)
    store.load()
    return store, StandardAnswer


@app.get("/api/v1/qa/answers")
async def list_answers(
    category: str = "",
    keyword: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 50,
):
    """标准答案列表"""
    store, _ = _get_answer_store()
    if status:
        answers = store.list_by_status(status)
    elif keyword:
        answers = store.search(keyword)
    elif category:
        answers = store.list_by_category(category)
    else:
        answers = store.list_all()
    # 分页
    total = len(answers)
    start = (page - 1) * page_size
    items = answers[start : start + page_size]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [a.to_dict() for a in items],
    }


@app.post("/api/v1/qa/answers")
async def add_answer(req: dict):
    """添加标准答案"""
    store, std_answer_cls = _get_answer_store()
    q = req.get("question", "").strip()
    a = req.get("answer", "").strip()
    if not q or not a:
        raise HTTPException(status_code=400, detail="question 和 answer 为必填字段")
    answer = std_answer_cls(
        question=q,
        answer=a,
        category=req.get("category", ""),
        source=req.get("source", "api"),
        match_strategy=req.get("match_strategy", "both"),
    )
    is_new = store.add(answer)
    return {"id": answer.id, "created": is_new, "question": q}


@app.delete("/api/v1/qa/answers/{answer_id}")
async def delete_answer(answer_id: str):
    """删除标准答案"""
    store, _ = _get_answer_store()
    if store.remove(answer_id):
        return {"deleted": True, "id": answer_id}
    raise HTTPException(status_code=404, detail="未找到该标准答案")


@app.patch("/api/v1/qa/answers/{answer_id}/status")
async def set_answer_status(answer_id: str, req: dict):
    """启用/禁用标准答案"""
    store, _ = _get_answer_store()
    new_status = req.get("status", "")
    if new_status not in ("enabled", "disabled"):
        raise HTTPException(status_code=400, detail="status 必须为 enabled 或 disabled")
    if store.set_status(answer_id, new_status):
        return {"id": answer_id, "status": new_status}
    raise HTTPException(status_code=404, detail="未找到该标准答案")


@app.post("/api/v1/qa/answers/{answer_id}/aliases")
async def add_answer_alias(answer_id: str, req: dict):
    """添加别名问题"""
    store, _ = _get_answer_store()
    alias_q = req.get("alias_question", "").strip()
    score = req.get("similarity_score", 1.0)
    if not alias_q:
        raise HTTPException(status_code=400, detail="alias_question 为必填字段")
    if store.add_alias(answer_id, alias_q, similarity_score=score):
        return {"id": answer_id, "alias_question": alias_q}
    raise HTTPException(status_code=404, detail="未找到该标准答案")


@app.get("/api/v1/qa/reviews")
async def list_reviews(status: str = "", page: int = 1, page_size: int = 20):
    """审核队列列表"""
    from qa.pipelines.components.review_queue import ReviewWorkflow

    workflow = ReviewWorkflow()
    workflow.ensure_loaded()
    items = workflow.store.list_all(status=status or None)
    total = len(items)
    start = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [i.to_dict() for i in items[start : start + page_size]],
    }


@app.post("/api/v1/qa/reviews/{item_id}/label")
async def label_review(item_id: str, req: dict):
    """标注审核项"""
    from qa.pipelines.components.early_exit import StandardAnswerStore
    from qa.pipelines.components.review_queue import ReviewWorkflow

    settings = get_settings()
    workflow = ReviewWorkflow()
    workflow.ensure_loaded()
    label = req.get("label", "")
    std_store = StandardAnswerStore(store_path=settings.early_exit.store_path)
    if workflow.label(
        item_id,
        label,
        reviewer=req.get("reviewer", "web"),
        comment=req.get("comment", ""),
        standard_answer_store=std_store,
    ):
        return {"id": item_id, "label": label}
    raise HTTPException(status_code=400, detail="标注失败（可能已审核或不存在）")


@app.get("/api/v1/qa/reviews/stats")
async def review_stats():
    """审核统计"""
    from qa.pipelines.components.review_queue import ReviewWorkflow

    workflow = ReviewWorkflow()
    workflow.ensure_loaded()
    return workflow.get_stats().to_dict()


@app.get("/api/v1/qa/metrics")
async def metrics():
    """可观测性指标"""
    from qa.pipelines.components.tracing import get_metrics

    return get_metrics().snapshot().to_dict()


@app.get("/api/v1/qa/health")
async def health():
    """健康检查"""
    return {"status": "ok", "version": "0.1.0"}


# 模块最末尾挂载前端（所有 API 路由已注册完毕）
_mount_frontend(app)


# ─── 直接启动 ─────────────────────────────────────────


def main():
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
