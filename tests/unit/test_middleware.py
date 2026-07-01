"""
API Gateway 中间件单元测试 — 认证 + 限流 + 请求日志

测试:
  - require_api_key: 无配置跳过 / 有效key / 无效key / 路径白名单
  - RateLimiter: 滑动窗口 / 超限429 / reset
  - mask_key: 脱敏工具
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

# Mock embedder before any qa imports
embedder_mock = mock.MagicMock()
sys.modules["qa.pipelines.components.embedder"] = embedder_mock

# Ensure src in path
src_path = str(Path(__file__).resolve().parent.parent / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)


# ═══════════════════════════════════════════════════════════════
# mask_key
# ═══════════════════════════════════════════════════════════════


class TestMaskKey:
    def test_none(self):
        from qa.api.middleware import mask_key

        assert mask_key(None) == "(none)"

    def test_empty(self):
        from qa.api.middleware import mask_key

        assert mask_key("") == "(none)"

    def test_short_key(self):
        from qa.api.middleware import mask_key

        assert mask_key("abc") == "****"

    def test_normal_key(self):
        from qa.api.middleware import mask_key

        assert mask_key("sk-1234567890") == "sk-1****"


# ═══════════════════════════════════════════════════════════════
# RateLimiter
# ═══════════════════════════════════════════════════════════════


class TestRateLimiter:
    def test_under_limit(self):
        from qa.api.middleware import RateLimiter

        limiter = RateLimiter(rpm=5, window_seconds=60)
        for _ in range(4):
            allowed, remaining, limit = limiter.check("test_key")
            assert allowed is True
            assert limit == 5

    def test_at_limit(self):
        from qa.api.middleware import RateLimiter

        limiter = RateLimiter(rpm=3, window_seconds=60)
        limiter.check("test_key")
        limiter.check("test_key")
        limiter.check("test_key")
        allowed, remaining, limit = limiter.check("test_key")
        assert allowed is False
        assert remaining == 0
        assert limit == 3

    def test_different_keys_independent(self):
        from qa.api.middleware import RateLimiter

        limiter = RateLimiter(rpm=2, window_seconds=60)
        limiter.check("key_a")
        limiter.check("key_a")
        # key_a at limit
        allowed_a, _, _ = limiter.check("key_a")
        assert allowed_a is False
        # key_b still has quota
        allowed_b, remaining_b, _ = limiter.check("key_b")
        assert allowed_b is True
        assert remaining_b == 1

    def test_zero_rpm_unlimited(self):
        from qa.api.middleware import RateLimiter

        limiter = RateLimiter(rpm=0)
        allowed, _, _ = limiter.check("test_key")
        assert allowed is True

    def test_reset_single_key(self):
        from qa.api.middleware import RateLimiter

        limiter = RateLimiter(rpm=2, window_seconds=60)
        limiter.check("key_a")
        limiter.check("key_a")
        limiter.reset("key_a")
        allowed, remaining, _ = limiter.check("key_a")
        assert allowed is True
        assert remaining == 1

    def test_reset_all(self):
        from qa.api.middleware import RateLimiter

        limiter = RateLimiter(rpm=2, window_seconds=60)
        limiter.check("key_a")
        limiter.check("key_b")
        limiter.reset()
        allowed_a, remaining_a, _ = limiter.check("key_a")
        allowed_b, remaining_b, _ = limiter.check("key_b")
        assert allowed_a is True
        assert allowed_b is True
        assert remaining_a == 1
        assert remaining_b == 1


# ═══════════════════════════════════════════════════════════════
# require_api_key (via FastAPI TestClient)
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def mock_settings_no_keys():
    """无 API keys 配置"""
    with mock.patch("qa.api.middleware.get_settings") as m:
        m.return_value.server.api_keys = []
        m.return_value.server.rate_limit_rpm = 0
        yield m


@pytest.fixture
def mock_settings_with_keys():
    """有 API keys 配置"""
    with mock.patch("qa.api.middleware.get_settings") as m:
        m.return_value.server.api_keys = ["valid-key-123", "another-key-456"]
        m.return_value.server.rate_limit_rpm = 0
        yield m


class TestRequireApiKey:
    def test_no_configured_keys_bypass(self, mock_settings_no_keys):
        """未配置 keys 时跳过鉴权"""
        import asyncio

        from fastapi import Request

        from qa.api.middleware import require_api_key

        request = mock.MagicMock(spec=Request)
        request.url.path = "/api/v1/qa/ask"
        result = asyncio.run(require_api_key(request, api_key=None))
        assert result is None

    def test_valid_key_passes(self, mock_settings_with_keys):
        """有效 key 通过鉴权"""
        import asyncio

        from fastapi import Request

        from qa.api.middleware import require_api_key

        request = mock.MagicMock(spec=Request)
        request.url.path = "/api/v1/qa/ask"
        result = asyncio.run(require_api_key(request, api_key="valid-key-123"))
        assert result == "valid-key-123"

    def test_invalid_key_rejected(self, mock_settings_with_keys):
        """无效 key 返回 401"""
        import asyncio

        from fastapi import HTTPException, Request

        from qa.api.middleware import require_api_key

        request = mock.MagicMock(spec=Request)
        request.url.path = "/api/v1/qa/ask"

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(require_api_key(request, api_key="invalid-key"))
        assert exc_info.value.status_code == 401

    def test_missing_key_rejected(self, mock_settings_with_keys):
        """缺少 key 返回 401"""
        import asyncio

        from fastapi import HTTPException, Request

        from qa.api.middleware import require_api_key

        request = mock.MagicMock(spec=Request)
        request.url.path = "/api/v1/qa/ask"

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(require_api_key(request, api_key=None))
        assert exc_info.value.status_code == 401

    def test_bypass_health(self, mock_settings_with_keys):
        """health 路径跳过鉴权"""
        import asyncio

        from fastapi import Request

        from qa.api.middleware import require_api_key

        request = mock.MagicMock(spec=Request)
        request.url.path = "/api/v1/qa/health"
        result = asyncio.run(require_api_key(request, api_key=None))
        assert result is None

    def test_bypass_status(self, mock_settings_with_keys):
        """status 路径跳过鉴权"""
        import asyncio

        from fastapi import Request

        from qa.api.middleware import require_api_key

        request = mock.MagicMock(spec=Request)
        request.url.path = "/api/v1/qa/status"
        result = asyncio.run(require_api_key(request, api_key=None))
        assert result is None

    def test_bypass_metrics(self, mock_settings_with_keys):
        """metrics 路径跳过鉴权"""
        import asyncio

        from fastapi import Request

        from qa.api.middleware import require_api_key

        request = mock.MagicMock(spec=Request)
        request.url.path = "/api/v1/qa/metrics"
        result = asyncio.run(require_api_key(request, api_key=None))
        assert result is None

    def test_bypass_docs(self, mock_settings_with_keys):
        """docs 路径跳过鉴权"""
        import asyncio

        from fastapi import Request

        from qa.api.middleware import require_api_key

        request = mock.MagicMock(spec=Request)
        request.url.path = "/docs"
        result = asyncio.run(require_api_key(request, api_key=None))
        assert result is None
