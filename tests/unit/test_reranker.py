"""
Reranker 精排组件单元测试（M6 review finding）

覆盖:
- Reranker 初始化（默认/自定义参数）
- session 懒加载
- rerank 方法（空文档/单文档/多文档/回退排序）
- _call_rerank_api API 响应解析
- 错误处理（超时/HTTP错误/异常回退）
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from haystack import Document

from qa.pipelines.components.reranker import Reranker

# ─── Fixtures ────────────────────────────────────────────


@pytest.fixture
def reranker():
    return Reranker(model="test-model", api_base_url="http://test", api_key="sk-test")


@pytest.fixture
def sample_docs():
    return [
        Document(content="证券清算规则第3条", meta={"file_path": "rules.md"}),
        Document(content="交收周期说明", meta={"file_path": "settlement.md"}),
        Document(content="登记托管流程", meta={"file_path": "custody.md"}),
    ]


# ─── 初始化 ────────────────────────────────────────────


class TestRerankerInit:
    def test_default_params(self):
        rr = Reranker()
        assert rr.model == "Qwen/Qwen3-Reranker-4B"
        assert rr.top_k == 5
        assert rr.timeout == 30
        assert rr.max_retries == 2
        assert rr._session is None

    def test_custom_params(self):
        rr = Reranker(
            model="custom-model",
            api_base_url="http://custom",
            api_key="sk-custom",
            top_k=10,
            timeout=60,
            max_retries=3,
        )
        assert rr.model == "custom-model"
        assert rr.api_base_url == "http://custom"
        assert rr.api_key == "sk-custom"
        assert rr.top_k == 10
        assert rr.timeout == 60
        assert rr.max_retries == 3


# ─── Session ────────────────────────────────────────────


class TestSession:
    def test_session_lazy_initialization(self):
        rr = Reranker()
        assert rr._session is None
        s = rr.session
        assert s is not None
        # 第二次访问返回相同实例
        assert rr.session is s

    def test_session_singleton(self):
        rr = Reranker()
        s1 = rr.session
        s2 = rr.session
        assert s1 is s2


# ─── rerank 方法 ─────────────────────────────────────────


class TestRerank:
    def test_empty_documents_returns_empty(self, reranker):
        result = reranker.rerank("测试", [])
        assert result == []

    def test_single_document(self, reranker):
        """单文档时调用 API 并返回排序结果"""
        docs = [Document(content="清算规则")]
        reranker._call_rerank_api = MagicMock(return_value=[0.95])
        result = reranker.rerank("清算", docs, top_k=5)
        assert len(result) == 1
        assert result[0].score == 0.95

    def test_multiple_documents_scored_and_sorted(self, reranker):
        """多文档按分数降序排列"""
        docs = [
            Document(content="规则A"),
            Document(content="规则B"),
            Document(content="规则C"),
        ]
        rr = reranker
        rr._call_rerank_api = MagicMock(return_value=[0.3, 0.9, 0.6])
        result = rr.rerank("测试", docs, top_k=3)
        assert len(result) == 3
        assert result[0].score == 0.9  # 最高分排第一
        assert result[2].score == 0.3  # 最低分排最后

    def test_top_k_truncates_results(self, reranker):
        docs = [Document(content=f"文档{i}") for i in range(5)]
        reranker._call_rerank_api = MagicMock(return_value=[0.9, 0.1, 0.8, 0.2, 0.7])
        result = reranker.rerank("测试", docs, top_k=2)
        assert len(result) == 2

    def test_rerank_score_stored_in_meta(self, reranker, sample_docs):
        reranker._call_rerank_api = MagicMock(return_value=[0.9, 0.8, 0.7])
        result = reranker.rerank("测试", sample_docs, top_k=3)
        assert result[0].meta["rerank_score"] == 0.9

    def test_fallback_to_original_on_api_failure(self, reranker, sample_docs):
        """API 失败时回退到原始排序（前 top_k 条）"""
        reranker._call_rerank_api = MagicMock(side_effect=Exception("API不可用"))
        result = reranker.rerank("测试", sample_docs, top_k=3)
        assert len(result) == 3
        # 原始顺序：按传入顺序保留
        assert result[0].content == "证券清算规则第3条"

    def test_top_k_default_from_constructor(self, reranker):
        """不传 top_k 时使用构造时的默认值"""
        reranker._call_rerank_api = MagicMock(return_value=[0.5, 0.5])
        docs = [Document(content="A"), Document(content="B")]
        result = reranker.rerank("测试", docs)
        assert len(result) <= 2

    def test_documents_unchanged_when_fallback(self, reranker, sample_docs):
        """回退时不应修改原始文档"""
        original_contents = [d.content for d in sample_docs]
        reranker._call_rerank_api = MagicMock(side_effect=Exception("fallback"))
        result = reranker.rerank("测试", sample_docs)
        assert [d.content for d in result] == original_contents[:3]


# ─── _call_rerank_api ───────────────────────────────────


class TestCallRerankApi:
    def test_standard_results_format(self, reranker):
        """标准格式: {'results': [{'index': 0, 'relevance_score': 0.9}]}"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.3},
            ]
        }
        reranker._session = MagicMock()
        reranker._session.post.return_value = mock_response

        scores = reranker._call_rerank_api(
            query="测试",
            documents=["doc1", "doc2"],
            base_url="http://test",
            api_key="sk-test",
        )
        assert scores == [0.9, 0.3]

    def test_openai_data_format(self, reranker):
        """OpenAI 兼容格式: {'data': [{'index': 0, 'score': 0.9}]}"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"index": 0, "score": 0.85},
                {"index": 1, "score": 0.25},
            ]
        }
        reranker._session = MagicMock()
        reranker._session.post.return_value = mock_response

        scores = reranker._call_rerank_api(
            query="测试",
            documents=["doc1", "doc2"],
            base_url="http://test",
            api_key="sk-test",
        )
        assert scores == [0.85, 0.25]

    def test_unknown_format_returns_zeros(self, reranker):
        """未知返回格式时以零填充"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"unknown": "format"}
        reranker._session = MagicMock()
        reranker._session.post.return_value = mock_response

        scores = reranker._call_rerank_api(
            query="测试",
            documents=["doc1", "doc2"],
            base_url="http://test",
            api_key="sk-test",
        )
        assert scores == [0.0, 0.0]

    def test_timeout_raises(self, reranker):
        """超时异常向上传播"""
        reranker._session = MagicMock()
        reranker._session.post.side_effect = TimeoutError("timeout")

        with pytest.raises(Exception):
            reranker._call_rerank_api(
                query="测试",
                documents=["doc1"],
                base_url="http://test",
                api_key="sk-test",
            )

    def test_api_url_construction(self, reranker):
        """验证 API URL 拼接正确"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        reranker._session = MagicMock()
        reranker._session.post.return_value = mock_response

        reranker._call_rerank_api(
            query="测试",
            documents=["doc1"],
            base_url="http://test/v1",
            api_key="sk-test",
        )

        # 验证 POST URL
        call_url = reranker._session.post.call_args[0][0]
        assert call_url == "http://test/v1/rerank"

    def test_auth_header_sent(self, reranker):
        """验证认证头正确发送"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        reranker._session = MagicMock()
        reranker._session.post.return_value = mock_response

        reranker._call_rerank_api(
            query="测试",
            documents=["doc1"],
            base_url="http://test",
            api_key="sk-secret",
        )

        call_headers = reranker._session.post.call_args[1]["headers"]
        assert "Authorization" in call_headers
        assert "Bearer sk-secret" in call_headers["Authorization"]

    def test_top_n_equals_document_count(self, reranker):
        """验证 payload 中 top_n 等于文档数"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        reranker._session = MagicMock()
        reranker._session.post.return_value = mock_response

        reranker._call_rerank_api(
            query="测试",
            documents=["doc1", "doc2", "doc3"],
            base_url="http://test",
            api_key="sk-test",
        )

        call_payload = reranker._session.post.call_args[1]["json"]
        assert call_payload["top_n"] == 3
