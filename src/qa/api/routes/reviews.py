"""审核队列路由 — /reviews list/label/stats"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from qa.api.dependencies import reset_runtime_singletons
from qa.api.middleware import check_rate_limit
from qa.config.settings import get_settings
from qa.pipelines.components.review_queue import ReviewWorkflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/qa")


@router.get("/reviews")
async def list_reviews(
    status: str = "",
    page: int = 1,
    page_size: int = 20,
    _auth=Depends(check_rate_limit),
):
    """审核队列列表"""
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


@router.post("/reviews/{item_id}/label")
async def label_review(item_id: str, req: dict, _auth=Depends(check_rate_limit)):
    """标注审核项"""
    from qa.pipelines.components.early_exit import StandardAnswerStore

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
        reset_runtime_singletons()
        return {"id": item_id, "label": label}
    raise HTTPException(status_code=400, detail="标注失败（可能已审核或不存在）")


@router.get("/reviews/stats")
async def review_stats(_auth=Depends(check_rate_limit)):
    """审核统计"""
    workflow = ReviewWorkflow()
    workflow.ensure_loaded()
    return workflow.get_stats().to_dict()
