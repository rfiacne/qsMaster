"""
运维端点 API 契约测试 — /health, /ready, /metrics

测试:
  - GET /health — 健康检查（轻量）
  - GET /ready — 就绪探针（深度检查）
  - GET /metrics — 可观测性指标
"""

from __future__ import annotations


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/api/v1/qa/health")
        assert resp.status_code == 200

    def test_health_response_body(self, client):
        data = resp = client.get("/api/v1/qa/health").json()
        assert data["status"] == "ok"
        assert "version" in data


class TestReadinessEndpoint:
    def test_ready_returns_response(self, client):
        """就绪探针返回 JSON（store 可能 mock 但不应崩溃）"""
        resp = client.get("/api/v1/qa/ready")
        assert resp.status_code in (200, 503)
        data = resp.json()
        assert "status" in data
        assert "version" in data

    def test_ready_contains_checks(self, client):
        """就绪探针包含各依赖检查"""
        resp = client.get("/api/v1/qa/ready")
        data = resp.json()
        if data["status"] == "ok":
            assert "checks" in data


class TestMetricsEndpoint:
    def test_metrics_returns_response(self, client):
        """指标端点返回 JSON"""
        resp = client.get("/api/v1/qa/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
