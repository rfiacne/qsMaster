"""
QA 问答 API 契约测试 — /status, /ask, /ask/stream, /search

测试:
  - GET /status — 知识库状态
  - POST /ask — 问答（mock pipeline）
  - POST /ask/stream — 流式问答（SSE）
  - POST /search — 检索
  - 错误处理
"""

from __future__ import annotations

from unittest import mock


class TestStatusEndpoint:
    def test_status_returns_store_info(self, client):
        """知识库状态返回 store 信息"""
        with mock.patch("qa.api.routes.qa.get_store") as mock_get_store:
            mock_store = mock.MagicMock()
            mock_status = mock.MagicMock()
            mock_status.document_count = 10
            mock_status.chunk_count = 100
            mock_status.index_size_bytes = 1024000
            mock_status.last_updated = "2026-06-01T00:00:00"
            mock_status.bit_width = 4
            mock_status.dim = 1024
            mock_status.persist_path = "/data/index"
            mock_store.get_status.return_value = mock_status
            mock_get_store.return_value = mock_store

            resp = client.get("/api/v1/qa/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["document_count"] == 10
            assert data["chunk_count"] == 100
            assert data["bit_width"] == 4
            assert data["dim"] == 1024


class TestAskEndpoint:
    def test_ask_returns_answer(self, client):
        """正常问答返回结果"""
        with (
            mock.patch("qa.api.routes.qa.get_query_pipeline") as mock_get_pipe,
            mock.patch("qa.api.routes.qa.get_store"),
        ):
            mock_result = mock.MagicMock()
            mock_result.to_dict.return_value = {
                "answer": "清算是T+1",
                "sources": [],
                "faithfulness": {"result": "PASS", "score": 0.9},
            }
            mock_pipeline = mock.MagicMock()
            mock_pipeline.run.return_value = mock_result

            async def async_run_side(**kw):
                return mock_pipeline.run(**kw)
            mock_pipeline.async_run = async_run_side

            mock_get_pipe.return_value = mock_pipeline

            resp = client.post(
                "/api/v1/qa/ask",
                json={"question": "清算流程是什么？"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["answer"] == "清算是T+1"

    def test_ask_with_parameters(self, client):
        """带 top_k 和 filters 参数"""
        with (
            mock.patch("qa.api.routes.qa.get_query_pipeline") as mock_get_pipe,
            mock.patch("qa.api.routes.qa.get_store"),
        ):
            mock_result = mock.MagicMock()
            mock_result.to_dict.return_value = {"answer": "结果", "sources": []}
            mock_pipeline = mock.MagicMock()
            mock_pipeline.run.return_value = mock_result

            async def async_run_side(**kw):
                return mock_pipeline.run(**kw)
            mock_pipeline.async_run = async_run_side

            mock_get_pipe.return_value = mock_pipeline

            resp = client.post(
                "/api/v1/qa/ask",
                json={
                    "question": "测试",
                    "top_k": 10,
                    "filters": {"category": "clearing_rule"},
                },
            )
            assert resp.status_code == 200

    def test_ask_pipeline_error(self, client):
        """pipeline 异常返回错误"""
        with (
            mock.patch("qa.api.routes.qa.get_query_pipeline") as mock_get_pipe,
            mock.patch("qa.api.routes.qa.get_store"),
        ):
            mock_pipeline = mock.MagicMock()
            mock_pipeline.run.side_effect = RuntimeError("Pipeline 失败")
            mock_get_pipe.return_value = mock_pipeline

            resp = client.post(
                "/api/v1/qa/ask",
                json={"question": "测试"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data


class TestSearchEndpoint:
    def test_search_returns_results(self, client):
        """检索返回文件分组结果"""
        with (
            mock.patch("qa.api.routes.qa.get_query_pipeline") as mock_get_pipe,
            mock.patch("qa.api.routes.qa.get_store"),
        ):
            # 构造 mock source 对象
            mock_source = mock.MagicMock()
            mock_source.score = 0.05
            mock_source.file_name = "/path/to/test.pdf"
            mock_source.content = "测试内容"
            mock_source.source = "CSDC"
            mock_source.category = "rule"
            mock_source.effective_date = "2026-06-01"

            mock_result = mock.MagicMock()
            mock_result.sources = [mock_source]
            mock_result.retrieval_time_ms = 50.0

            mock_pipeline = mock.MagicMock()
            mock_pipeline.run.return_value = mock_result

            async def async_run_side(**kw):
                return mock_pipeline.run(**kw)
            mock_pipeline.async_run = async_run_side

            mock_get_pipe.return_value = mock_pipeline

            resp = client.post(
                "/api/v1/qa/search",
                json={"query": "清算"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["query"] == "清算"
            assert "results" in data
            assert "score_range" in data

    def test_search_empty_results(self, client):
        """空检索结果"""
        with (
            mock.patch("qa.api.routes.qa.get_query_pipeline") as mock_get_pipe,
            mock.patch("qa.api.routes.qa.get_store"),
        ):
            mock_result = mock.MagicMock()
            mock_result.sources = []
            mock_result.retrieval_time_ms = 10.0

            mock_pipeline = mock.MagicMock()
            mock_pipeline.run.return_value = mock_result

            async def async_run_side(**kw):
                return mock_pipeline.run(**kw)
            mock_pipeline.async_run = async_run_side

            mock_get_pipe.return_value = mock_pipeline

            resp = client.post(
                "/api/v1/qa/search",
                json={"query": "不存在的内容"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["files"] == 0
            assert data["results"] == []

    def test_search_pipeline_error(self, client):
        """检索 pipeline 异常"""
        with (
            mock.patch("qa.api.routes.qa.get_query_pipeline") as mock_get_pipe,
            mock.patch("qa.api.routes.qa.get_store"),
        ):
            mock_pipeline = mock.MagicMock()
            mock_pipeline.run.side_effect = RuntimeError("检索失败")
            mock_get_pipe.return_value = mock_pipeline

            resp = client.post(
                "/api/v1/qa/search",
                json={"query": "测试"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data


class TestStreamEndpoint:
    def test_stream_returns_sse(self, client):
        """流式问答返回 SSE 响应"""
        with (
            mock.patch("qa.api.routes.qa.get_query_pipeline") as mock_get_pipe,
            mock.patch("qa.api.routes.qa.get_store"),
            mock.patch("qa.api.routes.qa.SessionStore") as mock_session_cls,
        ):
            # Mock session store
            mock_store = mock.MagicMock()
            mock_session = mock.MagicMock()
            mock_session.id = "test_session_123"
            mock_store.load.return_value = None
            mock_store.get.return_value = None
            mock_store.create.return_value = mock_session
            mock_session_cls.return_value = mock_store

            # Mock pipeline stream
            mock_pipeline = mock.MagicMock()
            mock_pipeline.run_stream.return_value = iter(
                [
                    {"type": "token", "content": "清算"},
                    {"type": "token", "content": "是T+1"},
                    {"type": "sources", "sources": []},
                    {"type": "done"},
                ]
            )
            mock_get_pipe.return_value = mock_pipeline

            resp = client.post(
                "/api/v1/qa/ask/stream",
                json={"question": "清算流程？"},
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            # 读取 SSE 内容
            body = resp.text
            assert "meta" in body  # 第一个事件是 meta
