"""
契约测试共享 conftest — 统一 mock 外部依赖并创建 FastAPI TestClient

所有 contract/ 下的测试文件共享此 fixture，避免每个文件重复 mock。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# ─── 在导入 qa 模块之前完成所有 mock ──────────────────────

# Mock embedder
embedder_mock = mock.MagicMock()
sys.modules["qa.pipelines.components.embedder"] = embedder_mock

# Mock settings
_settings_patcher = mock.patch("qa.config.settings.get_settings")
_mock_settings = _settings_patcher.start()
_mock_settings.return_value.llm.api_base_url = "http://test:8000/v1"
_mock_settings.return_value.llm.resolved_api_key = "test-key"
_mock_settings.return_value.llm.model = "test-model"
_mock_settings.return_value.embedding.api_base_url = "http://test:8000/v1"
_mock_settings.return_value.embedding.model = "test-model"
_mock_settings.return_value.embedding.resolved_api_key = "test-key"
_mock_settings.return_value.embedding.backend = "api"
_mock_settings.return_value.early_exit.store_path = tempfile.mkdtemp()
_mock_settings.return_value.vector_store.bit_width = 4
_mock_settings.return_value.vector_store.similarity_function = "cosine"
_mock_settings.return_value.vector_store.persist_path = tempfile.mkdtemp()
# API gateway: no auth, no rate limit for tests
_mock_settings.return_value.server.api_keys = []
_mock_settings.return_value.server.rate_limit_rpm = 0
_mock_settings.return_value.server.allowed_origins = ["*"]
_mock_settings.return_value.server.host = "127.0.0.1"
_mock_settings.return_value.server.port = 8001
_mock_settings.return_value.server.reload = False
_mock_settings.return_value.retrieval.min_score = 0.01
_mock_settings.return_value.retrieval.use_hybrid = True
_mock_settings.return_value.retrieval.block_sizes = [500, 100]
_mock_settings.return_value.indexing.ocr_enabled = False
_mock_settings.return_value.indexing.ocr_backend = "auto"
_mock_settings.return_value.indexing.doc_timeout_seconds = 120
_mock_settings.return_value.embedding.timeout_seconds = 30

# Also mock get_settings in middleware module (it imports separately)
_middleware_settings_patcher = mock.patch("qa.api.middleware.get_settings")
_mock_middleware_settings = _middleware_settings_patcher.start()
_mock_middleware_settings.return_value.server.api_keys = []
_mock_middleware_settings.return_value.server.rate_limit_rpm = 0
_mock_middleware_settings.return_value.server.allowed_origins = ["*"]

# Mock routes qa.py get_settings
_qa_routes_settings_patcher = mock.patch("qa.api.routes.qa.get_settings")
_mock_qa_settings = _qa_routes_settings_patcher.start()
_mock_qa_settings.return_value.retrieval.min_score = 0.01

# Mock routes upload.py get_settings and validate_meta
_upload_settings_patcher = mock.patch("qa.api.routes.upload.get_settings")
_mock_upload_settings = _upload_settings_patcher.start()
_mock_upload_settings.return_value.indexing.doc_timeout_seconds = 120
_mock_upload_settings.return_value.embedding.timeout_seconds = 30

# Mock routes ops.py get_settings
_ops_settings_patcher = mock.patch("qa.api.routes.ops.get_settings")
_mock_ops_settings = _ops_settings_patcher.start()
_mock_ops_settings.return_value.embedding.api_base_url = "http://test:8000/v1"
_mock_ops_settings.return_value.embedding.backend = "api"
_mock_ops_settings.return_value.llm.model = "test-model"
_mock_ops_settings.return_value.llm.api_base_url = "http://test:8000/v1"
_mock_ops_settings.return_value.llm.resolved_api_key = "test-key"

# Mock answers.py get_settings
_answers_settings_patcher = mock.patch("qa.api.routes.answers.get_settings")
_mock_answers_settings = _answers_settings_patcher.start()
_mock_answers_settings.return_value.early_exit.store_path = tempfile.mkdtemp()

# Mock reviews.py get_settings
_reviews_settings_patcher = mock.patch("qa.api.routes.reviews.get_settings")
_mock_reviews_settings = _reviews_settings_patcher.start()
_mock_reviews_settings.return_value.early_exit.store_path = tempfile.mkdtemp()

# Ensure src in path
_src_path = str(Path(__file__).resolve().parent.parent.parent / "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)


@pytest.fixture
def session_tmpdir():
    """临时会话存储目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def review_tmpdir():
    """临时审核队列目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def answer_tmpdir():
    """临时标准答案存储目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def client(review_tmpdir, answer_tmpdir, session_tmpdir):
    """FastAPI TestClient，所有存储指向临时目录"""
    from fastapi.testclient import TestClient

    from qa.api.server import app
    from qa.pipelines.components.early_exit import StandardAnswerStore
    from qa.pipelines.components.review_queue import ReviewWorkflow
    from qa.pipelines.components.session_store import SessionStore

    # Patch ReviewWorkflow
    original_review_init = ReviewWorkflow.__init__

    def patched_review_init(self, store_path=review_tmpdir, **kwargs):
        original_review_init(self, store_path=review_tmpdir, **kwargs)

    # Patch SessionStore
    original_session_init = SessionStore.__init__

    def patched_session_init(self, store_path=session_tmpdir, **kwargs):
        original_session_init(self, store_path=session_tmpdir, **kwargs)

    # Patch StandardAnswerStore
    original_answer_init = StandardAnswerStore.__init__

    def patched_answer_init(self, store_path=answer_tmpdir, **kwargs):
        original_answer_init(self, store_path=answer_tmpdir, **kwargs)

    with (
        mock.patch.object(ReviewWorkflow, "__init__", patched_review_init),
        mock.patch.object(SessionStore, "__init__", patched_session_init),
        mock.patch.object(StandardAnswerStore, "__init__", patched_answer_init),
    ):
        yield TestClient(app)
