"""
Securities QA Agent — FastAPI 后端服务

为前端 index.html 提供 REST API。
启动: uvicorn qa.api.server:app --host 0.0.0.0 --port 8001
或者: python -m qa.api.server
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import FastAPI, HTTPException, UploadFile, Form, File
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
    filters: Optional[Dict[str, Any]] = None
    no_llm: bool = False


class SearchRequest(BaseModel):
    query: str
    top_k: int = 20


# ─── 延迟初始化（首次请求时加载） ─────────────────────


_store = None
_query_pipeline = None
_index_pipeline = None


def get_store():
    global _store
    if _store is None:
        settings = get_settings()
        _store = create_store_manager(
            bit_width=settings.vector_store.bit_width,
            similarity_function=settings.vector_store.similarity_function,
            persist_path=settings.vector_store.persist_path,
        )
    return _store


def get_query_pipeline():
    global _query_pipeline
    if _query_pipeline is None:
        settings = get_settings()
        reranker = None
        if settings.rerank.enabled:
            from qa.pipelines.components.reranker import Reranker
            reranker = Reranker(
                model=settings.rerank.model,
                api_base_url=settings.rerank.api_base_url,
                api_key=settings.rerank.resolved_api_key,
                top_k=settings.rerank.top_k,
            )
        _query_pipeline = QueryPipeline(
            store_manager=get_store(),
            top_k=settings.retrieval.top_k,
            auto_merge_threshold=settings.retrieval.auto_merge_threshold,
            use_hybrid=settings.retrieval.use_hybrid,
            hybrid_vector_weight=settings.retrieval.hybrid_vector_weight,
            reranker=reranker,
        )
    return _query_pipeline


def get_index_pipeline():
    global _index_pipeline
    if _index_pipeline is None:
        settings = get_settings()
        _index_pipeline = IndexingPipeline(
            store_manager=get_store(),
            block_sizes=settings.retrieval.block_sizes,
            ocr_enabled=settings.indexing.ocr_enabled,
        )
    return _index_pipeline


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
    pipeline = get_query_pipeline()
    result = pipeline.run(
        question=req.question,
        top_k=req.top_k,
        filters=req.filters,
        no_llm=req.no_llm,
    )
    return result.to_dict()


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
    file_groups: Dict[str, list] = defaultdict(list)
    for s in filtered:
        file_groups[s.file_name].append(s)

    # 每个文件取最高分片段作为代表，统计 chunk 数
    from pathlib import Path as _Path
    grouped = []
    for file_name, chunks in file_groups.items():
        best = max(chunks, key=lambda x: x.score)
        # 只保留文件名
        short_name = _Path(file_name).name
        grouped.append({
            "file_name": short_name,
            "content": best.content,
            "source": best.source,
            "category": best.category,
            "effective_date": best.effective_date,
            "score": round(best.score, 4),
            "chunks": len(chunks),  # 该文件命中的片段数
        })

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
    files: List[UploadFile],
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
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"索引超时 ({pipe_timeout}s)。查看服务端日志确认卡在哪一步。"
            )

        # 显式持久化（StoreWriter 不再自动 save）
        store = get_store()
        await asyncio.to_thread(store.save)

        return {
            "files_count": result.files_count,
            "documents_written": result.documents_written,
            "documents_skipped": result.documents_skipped,
            "segments": result.chunk_count,       # 检索用片段数
            "parents": result.parent_count,       # 上下文用大块数
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

        app_instance.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
        app_instance.add_middleware(NoCacheMiddleware)
        print(f"前端静态文件已挂载: {frontend_dir}")
    except Exception as e:
        print(f"前端挂载失败: {e}")


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
