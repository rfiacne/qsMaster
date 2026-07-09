"""
契约测试共享 conftest — 统一 mock 外部依赖并创建 FastAPI TestClient

改进:
  - 用 mock_settings() 工厂替代 7 个独立 module-level patcher，消除维护脆弱性
  - 所有 route 模块共享同一 settings mock (patch the underlying module reference)
  - 每个测试独立使用 settings_mock fixture 进行 override
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# 确保 src 在路径中
_src_path = str(Path(__file__).resolve().parent.parent.parent / "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)


def default_mock_settings():
    """创建完整的 mock Settings 对象

    所有模块共享同一基值，测试可通过上下文管理器局部 override。
    """
    s = mock.MagicMock()
    s.llm.api_base_url = "http://test:8000/v1"
    s.llm.resolved_api_key = "test-key"
    s.llm.model = "test-model"
    s.llm.timeout_seconds = 30
    s.llm.temperature = 0.0
    s.llm.max_tokens = 2000
    s.embedding.api_base_url = "http://test:8000/v1"
    s.embedding.model = "test-model"
    s.embedding.resolved_api_key = "test-key"
    s.embedding.backend = "api"
    s.embedding.timeout_seconds = 30
    s.early_exit.store_path = tempfile.mkdtemp()
    s.vector_store.bit_width = 4
    s.vector_store.similarity_function = "cosine"
    s.vector_store.persist_path = tempfile.mkdtemp()
    s.server.api_keys = []
    s.server.rate_limit_rpm = 0
    s.server.allowed_origins = ["*"]
    s.server.host = "127.0.0.1"
    s.server.port = 8001
    s.server.reload = False
    s.retrieval.min_score = 0.01
    s.retrieval.use_hybrid = True
    s.retrieval.block_sizes = [500, 100]
    s.retrieval.top_k = 5
    s.retrieval.auto_merge_threshold = 0.5
    s.retrieval.rrf_k = 35
    s.indexing.ocr_enabled = False
    s.indexing.ocr_backend = "auto"
    s.indexing.doc_timeout_seconds = 120
    s.faithfulness.enabled = False
    s.rerank.enabled = False
    s.query_rewrite.enabled = False
    s.query_cache.enabled = False
    s.otel.enabled = False
    return s


# 模块级 mock — 所有 route 模块导入时看到 mock 而非真实配置
_mock_settings = default_mock_settings()
_settings_patcher = mock.patch("qa.config.settings.get_settings", return_value=_mock_settings)
_settings_patcher.start()

# embedder 模块级 mock（避免导入 haystack/torch 依赖）
sys.modules["qa.pipelines.components.embedder"] = mock.MagicMock()

# mock 路由模块中的 get_settings (各模块 import 后持有独立引用)
_modules_to_patch = [
    "qa.api.middleware",
    "qa.api.routes.qa",
    "qa.api.routes.upload",
    "qa.api.routes.ops",
    "qa.api.routes.answers",
    "qa.api.routes.reviews",
    "qa.api.dependencies",
]
for _mod in _modules_to_patch:
    _p = mock.patch(f"{_mod}.get_settings", return_value=_mock_settings)
    _p.start()


@pytest.fixture
def settings_mock():
    """返回当前共享的 mock settings 的副本，测试可局部 override 而不会污染其他测试"""
    import copy

    return copy.deepcopy(_mock_settings)


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

    # Track created instances to close them before tmpdir cleanup
    created_workflows: list[ReviewWorkflow] = []
    created_sessions: list[SessionStore] = []

    original_review_init = ReviewWorkflow.__init__
    original_session_init = SessionStore.__init__
    original_answer_init = StandardAnswerStore.__init__

    def patched_review_init(self, store_path=review_tmpdir, **kwargs):
        original_review_init(self, store_path=review_tmpdir, **kwargs)
        created_workflows.append(self)

    def patched_session_init(self, store_path=session_tmpdir, **kwargs):
        original_session_init(self, store_path=session_tmpdir, **kwargs)
        created_sessions.append(self)

    def patched_answer_init(self, store_path=answer_tmpdir, **kwargs):
        original_answer_init(self, store_path=answer_tmpdir, **kwargs)

    with (
        mock.patch.object(ReviewWorkflow, "__init__", patched_review_init),
        mock.patch.object(SessionStore, "__init__", patched_session_init),
        mock.patch.object(StandardAnswerStore, "__init__", patched_answer_init),
    ):
        yield TestClient(app)

    # Close all created stores to release SQLite WAL locks before tmpdir cleanup
    for wf in created_workflows:
        wf.store.close()
    for ss in created_sessions:
        ss.close()
