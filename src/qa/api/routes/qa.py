"""QA 问答与检索路由 — /status, /ask, /ask/stream, /search"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from qa.api.dependencies import (
    AskRequest,
    SearchRequest,
    get_query_pipeline,
    get_store,
)
from qa.api.middleware import check_rate_limit
from qa.config.settings import get_settings
from qa.pipelines.components.session_store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/qa")


@router.get("/status")
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


@router.post("/ask")
async def ask(req: AskRequest, _auth=Depends(check_rate_limit)):
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


@router.post("/ask/stream")
async def ask_stream(req: AskRequest, _auth=Depends(check_rate_limit)):
    """流式问答 — SSE 响应"""

    # 获取或创建会话
    session_store = SessionStore()
    session_store.load()
    session_id = req.session_id
    if session_id:
        session = session_store.get(session_id)
        if not session:
            session = session_store.create(title=req.question[:50])
            session_id = session.id
    else:
        session = session_store.create(title=req.question[:50])
        session_id = session.id

    async def event_generator():
        full_answer = ""
        sources = []

        # 发送会话 ID 作为第一个事件
        yield f"event: message\ndata: {json.dumps({'type': 'meta', 'session_id': session_id})}\n\n"

        try:
            pipeline = get_query_pipeline()
            for event in pipeline.run_stream(
                question=req.question,
                top_k=req.top_k,
                filters=req.filters,
            ):
                event_type = event.get("type", "data")
                data = json.dumps(event, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data}\n\n"

                # 收集回答内容用于保存会话
                if event_type == "token":
                    full_answer += event.get("text", "")
                elif event_type == "sources":
                    sources = event.get("sources", [])
                elif event_type == "done":
                    # 保存对话到会话
                    if full_answer:
                        session.add_turn(req.question, full_answer, sources)
                        session_store.save(session)

                if event_type == "error" or event_type == "done":
                    break
        except Exception as e:
            logger.error(f"流式问答异常: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/search")
async def search(req: SearchRequest, _auth=Depends(check_rate_limit)):
    """知识库检索（仅检索，不生成回答）"""
    try:
        pipeline = get_query_pipeline()
        result = pipeline.run(
            question=req.query,
            top_k=req.top_k,
            no_llm=True,
        )
    except Exception as e:
        logger.error(f"搜索请求失败: {e}", exc_info=True)
        return {"error": str(e), "code": "INTERNAL"}
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
