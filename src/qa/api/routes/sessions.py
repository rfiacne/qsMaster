"""会话管理路由 — /sessions CRUD"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from qa.api.middleware import check_rate_limit
from qa.pipelines.components.session_store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/qa")


@router.get("/sessions")
async def list_sessions(limit: int = 20, _auth=Depends(check_rate_limit)):
    """列出最近会话"""
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


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, _auth=Depends(check_rate_limit)):
    """删除会话"""
    store = SessionStore()
    store.load()
    if store.delete(session_id):
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="会话不存在")


@router.post("/sessions")
async def create_session(req: dict | None = None, _auth=Depends(check_rate_limit)):
    """创建新会话"""
    store = SessionStore()
    store.load()
    title = (req or {}).get("title", "")
    session = store.create(title=title)
    return {
        "id": session.id,
        "title": session.title or "(新会话)",
        "created_at": session.created_at,
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, _auth=Depends(check_rate_limit)):
    """获取会话详情（含对话历史）"""
    store = SessionStore()
    store.load()
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "id": session.id,
        "title": session.title or "(新会话)",
        "turns": [
            {
                "question": t.question,
                "answer": t.answer,
                "sources": t.sources,
                "timestamp": t.timestamp,
            }
            for t in session.turns
        ],
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


@router.post("/sessions/{session_id}/clear")
async def clear_session(session_id: str, _auth=Depends(check_rate_limit)):
    """清空会话对话历史（保留会话本身）"""
    store = SessionStore()
    store.load()
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.turns.clear()
    session.title = ""
    store.save(session)
    store.flush()
    return {"cleared": True, "id": session_id}
