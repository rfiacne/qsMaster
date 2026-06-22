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

# Mock settings
settings_patcher = mock.patch("qa.config.settings.get_settings")
mock_settings = settings_patcher.start()
mock_settings.return_value.llm.api_base_url = "http://test:8000/v1"
mock_settings.return_value.llm.resolved_api_key = "test-key"
mock_settings.return_value.llm.model = "test-model"
mock_settings.return_value.embedding.api_base_url = "http://test:8000/v1"
mock_settings.return_value.embedding.model = "test-model"
mock_settings.return_value.embedding.resolved_api_key = "test-key"
mock_settings.return_value.early_exit.store_path = tempfile.mkdtemp()
mock_settings.return_value.vector_store.bit_width = 4
mock_settings.return_value.vector_store.similarity_function = "cosine"
mock_settings.return_value.vector_store.persist_path = tempfile.mkdtemp()
# API gateway: no auth, no rate limit for tests
mock_settings.return_value.server.api_keys = []
mock_settings.return_value.server.rate_limit_rpm = 0
mock_settings.return_value.server.allowed_origins = ["*"]

# Also mock get_settings in middleware module (it imports separately)
middleware_settings_patcher = mock.patch("qa.api.middleware.get_settings")
mock_middleware_settings = middleware_settings_patcher.start()
mock_middleware_settings.return_value.server.api_keys = []
mock_middleware_settings.return_value.server.rate_limit_rpm = 0
mock_middleware_settings.return_value.server.allowed_origins = ["*"]

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

    # Patch ReviewWorkflow 使用临时目录
    original_init = ReviewWorkflow.__init__

    def patched_init(self, store_path=review_tmpdir, **kwargs):
        original_init(self, store_path=review_tmpdir, **kwargs)

    with mock.patch.object(ReviewWorkflow, "__init__", patched_init):
        yield TestClient(app)


@pytest.fixture
def seeded_review(client, review_tmpdir):
    """预置一条待审核项，返回 item_id"""
    from qa.pipelines.components.review_queue import ReviewWorkflow

    workflow = ReviewWorkflow(store_path=review_tmpdir)
    workflow.ensure_loaded()
    item_id = workflow.add_item(
        question="测试问题",
        answer="测试回答",
        faithfulness_score=0.3,
        faithfulness_result="fail",
        priority="high",
    )
    return item_id


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
