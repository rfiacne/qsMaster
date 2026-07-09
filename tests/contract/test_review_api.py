"""
审核 API 契约测试 — 验证 /api/v1/qa/reviews 端点的请求/响应契约

测试:
  - GET /reviews — 列表 + 分页 + 状态筛选
  - POST /reviews/{id}/label — 标注 + 400 错误
  - GET /reviews/stats — 统计
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Mock embedder before any qa imports
embedder_mock = mock.MagicMock()
sys.modules["qa.pipelines.components.embedder"] = embedder_mock

# 注意：本文件的 mock 已由 contract/conftest.py 统一管理。
# 以下保留仅为向后兼容（直接运行本文件时仍可用）。

# Ensure src in path
src_path = str(Path(__file__).resolve().parent.parent.parent / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)


@pytest.fixture
def review_tmpdir():
    """临时审核队列目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def client(review_tmpdir):
    """FastAPI TestClient，review 存储指向临时目录"""
    from fastapi.testclient import TestClient

    from qa.api.server import app
    from qa.pipelines.components.review_queue import ReviewWorkflow

    # Track created instances to close them before tmpdir cleanup
    created_workflows: list[ReviewWorkflow] = []

    original_init = ReviewWorkflow.__init__

    def patched_init(self, store_path=review_tmpdir, **kwargs):
        original_init(self, store_path=review_tmpdir, **kwargs)
        created_workflows.append(self)

    with mock.patch.object(ReviewWorkflow, "__init__", patched_init):
        yield TestClient(app)

    # Close all created stores to release SQLite WAL locks before tmpdir cleanup
    for wf in created_workflows:
        wf.store.close()


@pytest.fixture
def seeded_review(client, review_tmpdir):
    """预置一条待审核项，返回 item_id"""
    from qa.pipelines.components.review_queue import ReviewWorkflow

    with ReviewWorkflow(store_path=review_tmpdir) as workflow:
        workflow.ensure_loaded()
        item_id = workflow.add_item(
            question="测试问题",
            answer="测试回答",
            faithfulness_score=0.3,
            faithfulness_result="fail",
            priority="high",
        )
        yield item_id


class TestListReviews:
    def test_empty(self, client):
        resp = client.get("/api/v1/qa/reviews")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_with_items(self, client, seeded_review):
        resp = client.get("/api/v1/qa/reviews")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["question"] == "测试问题"
        assert data["items"][0]["priority"] == "high"

    def test_pagination(self, client, review_tmpdir):
        from qa.pipelines.components.review_queue import ReviewWorkflow

        workflow = ReviewWorkflow(store_path=review_tmpdir)
        workflow.ensure_loaded()
        for i in range(5):
            workflow.add_item(question=f"Q{i}", answer=f"A{i}")

        resp = client.get("/api/v1/qa/reviews?page=1&page_size=2")
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["page"] == 1

    def test_status_filter(self, client, seeded_review):
        resp = client.get("/api/v1/qa/reviews?status=pending")
        data = resp.json()
        assert data["total"] == 1


class TestLabelReview:
    def test_label_correct(self, client, seeded_review):
        resp = client.post(
            f"/api/v1/qa/reviews/{seeded_review}/label",
            json={"label": "correct", "reviewer": "tester", "comment": "OK"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == seeded_review
        assert data["label"] == "correct"

    def test_label_incorrect(self, client, seeded_review):
        resp = client.post(
            f"/api/v1/qa/reviews/{seeded_review}/label",
            json={"label": "incorrect", "reviewer": "tester"},
        )
        assert resp.status_code == 200

    def test_label_twice_rejected(self, client, seeded_review):
        client.post(
            f"/api/v1/qa/reviews/{seeded_review}/label",
            json={"label": "correct"},
        )
        resp = client.post(
            f"/api/v1/qa/reviews/{seeded_review}/label",
            json={"label": "incorrect"},
        )
        assert resp.status_code == 400

    def test_label_nonexistent(self, client):
        resp = client.post(
            "/api/v1/qa/reviews/nonexistent_id/label",
            json={"label": "correct"},
        )
        assert resp.status_code == 400

    def test_label_invalid_value(self, client, seeded_review):
        resp = client.post(
            f"/api/v1/qa/reviews/{seeded_review}/label",
            json={"label": "invalid_label"},
        )
        assert resp.status_code == 400


class TestReviewStats:
    def test_empty_stats(self, client):
        resp = client.get("/api/v1/qa/reviews/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert "completion_rate" in data

    def test_stats_after_label(self, client, seeded_review):
        client.post(
            f"/api/v1/qa/reviews/{seeded_review}/label",
            json={"label": "correct"},
        )
        resp = client.get("/api/v1/qa/reviews/stats")
        data = resp.json()
        assert data["total"] == 1
        assert data["correct"] == 1
