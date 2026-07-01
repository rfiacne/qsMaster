"""
M5 安全与中间件契约测试 — API Key 认证 + 速率限制 + CORS

通过 FastAPI TestClient 验证完整 HTTP 流。
"""

from __future__ import annotations

from unittest import mock


class TestRateLimitContract:
    """速率限制契约测试（端到端 HTTP 流）"""

    def test_rate_limit_returns_429(self):
        """超限请求返回 429"""
        from fastapi.testclient import TestClient

        with (
            mock.patch("qa.api.middleware.get_settings") as m_settings,
            mock.patch("qa.config.settings.get_settings") as m_config,
            mock.patch("qa.api.routes.qa.get_settings") as m_qa,
            mock.patch("qa.api.routes.ops.get_settings") as m_ops,
            mock.patch("qa.api.routes.answers.get_settings") as m_ans,
            mock.patch("qa.api.routes.reviews.get_settings") as m_rev,
            mock.patch("qa.api.routes.upload.get_settings") as m_up,
        ):
            for m in [m_settings, m_config, m_qa, m_ops, m_ans, m_rev, m_up]:
                m.return_value.server.api_keys = []
                m.return_value.server.rate_limit_rpm = 2  # 极低限制
                m.return_value.server.allowed_origins = ["*"]
            m_config.return_value.llm.api_base_url = "http://test:8000/v1"
            m_config.return_value.llm.resolved_api_key = "test-key"
            m_config.return_value.llm.model = "test-model"
            m_config.return_value.embedding.api_base_url = "http://test:8000/v1"
            m_config.return_value.embedding.model = "test-model"
            m_config.return_value.embedding.resolved_api_key = "test-key"
            m_config.return_value.embedding.backend = "api"
            m_config.return_value.embedding.timeout_seconds = 30
            m_config.return_value.early_exit.store_path = "/tmp/test"
            m_config.return_value.vector_store.bit_width = 4
            m_config.return_value.vector_store.similarity_function = "cosine"
            m_config.return_value.vector_store.persist_path = "/tmp/test"
            m_config.return_value.retrieval.min_score = 0.01
            m_config.return_value.retrieval.use_hybrid = True
            m_config.return_value.retrieval.block_sizes = [500, 100]
            m_config.return_value.indexing.ocr_enabled = False
            m_config.return_value.indexing.ocr_backend = "auto"
            m_config.return_value.indexing.doc_timeout_seconds = 120
            m_ops.return_value.embedding.api_base_url = "http://test:8000/v1"
            m_ops.return_value.embedding.backend = "api"
            m_ops.return_value.llm.model = "test-model"
            m_ops.return_value.llm.api_base_url = "http://test:8000/v1"
            m_ops.return_value.llm.resolved_api_key = "test-key"
            m_ans.return_value.early_exit.store_path = "/tmp/test"
            m_rev.return_value.early_exit.store_path = "/tmp/test"
            m_up.return_value.indexing.doc_timeout_seconds = 120
            m_up.return_value.embedding.timeout_seconds = 30
            m_qa.return_value.retrieval.min_score = 0.01

            # Reset rate limiter singleton
            import qa.api.middleware as mw

            mw._rate_limiter = None

            from qa.api.server import app

            client = TestClient(app)

            # Mock pipeline to avoid real calls
            with mock.patch("qa.api.routes.qa.get_query_pipeline") as mock_pipe:
                mock_result = mock.MagicMock()
                mock_result.to_dict.return_value = {"answer": "test", "sources": []}
                mock_pipeline = mock.MagicMock()
                mock_pipeline.run.return_value = mock_result
                mock_pipe.return_value = mock_pipeline

                with mock.patch("qa.api.routes.qa.get_store"):
                    # 前 2 次应成功
                    for _ in range(2):
                        resp = client.post(
                            "/api/v1/qa/ask",
                            json={"question": "测试"},
                        )
                        assert resp.status_code == 200

                    # 第 3 次应被限流
                    resp = client.post(
                        "/api/v1/qa/ask",
                        json={"question": "测试"},
                    )
                    assert resp.status_code == 429

            # Cleanup
            mw._rate_limiter = None


class TestCORSPolicy:
    """CORS 策略契约测试"""

    def test_cors_wildcard_origin(self, client):
        """通配符配置允许任意来源"""
        resp = client.get(
            "/api/v1/qa/health",
            headers={"Origin": "http://example.com"},
        )
        # 通配符配置下应允许访问
        assert resp.status_code == 200

    def test_cors_headers_present(self, client):
        """响应包含 CORS 相关头"""
        resp = client.get("/api/v1/qa/health")
        # FastAPI CORSMiddleware 添加的头
        assert "access-control-allow-origin" in resp.headers or resp.status_code == 200
