"""标准答案库路由 — /answers CRUD + 别名"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from qa.api.middleware import check_rate_limit
from qa.config.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/qa")


def _get_answer_store():
    """获取标准答案库存储"""
    from qa.pipelines.components.early_exit import StandardAnswer, StandardAnswerStore

    settings = get_settings()
    store = StandardAnswerStore(store_path=settings.early_exit.store_path)
    store.load()
    return store, StandardAnswer


@router.get("/answers")
async def list_answers(
    _auth=Depends(check_rate_limit),
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


@router.post("/answers")
async def add_answer(req: dict, _auth=Depends(check_rate_limit)):
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


@router.delete("/answers/{answer_id}")
async def delete_answer(answer_id: str, _auth=Depends(check_rate_limit)):
    """删除标准答案"""
    store, _ = _get_answer_store()
    if store.remove(answer_id):
        return {"deleted": True, "id": answer_id}
    raise HTTPException(status_code=404, detail="未找到该标准答案")


@router.patch("/answers/{answer_id}/status")
async def set_answer_status(answer_id: str, req: dict, _auth=Depends(check_rate_limit)):
    """启用/禁用标准答案"""
    store, _ = _get_answer_store()
    new_status = req.get("status", "")
    if new_status not in ("enabled", "disabled"):
        raise HTTPException(status_code=400, detail="status 必须为 enabled 或 disabled")
    if store.set_status(answer_id, new_status):
        return {"id": answer_id, "status": new_status}
    raise HTTPException(status_code=404, detail="未找到该标准答案")


@router.post("/answers/{answer_id}/aliases")
async def add_answer_alias(answer_id: str, req: dict, _auth=Depends(check_rate_limit)):
    """添加别名问题"""
    store, _ = _get_answer_store()
    alias_q = req.get("alias_question", "").strip()
    score = req.get("similarity_score", 1.0)
    if not alias_q:
        raise HTTPException(status_code=400, detail="alias_question 为必填字段")
    if store.add_alias(answer_id, alias_q, similarity_score=score):
        return {"id": answer_id, "alias_question": alias_q}
    raise HTTPException(status_code=404, detail="未找到该标准答案")
