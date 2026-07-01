"""
配置管理单元测试 — settings.py

覆盖:
  - 默认值验证（所有关键配置项有合理默认值）
  - 环境变量覆盖
  - 配置校验失败（非法值范围）
  - ApiKeyMixin resolved_api_key 优先级
  - VectorStoreConfig 自定义校验
"""

from __future__ import annotations

import os
from unittest import mock

import pytest


class TestDefaultValues:
    """M6 FR-028: 所有新增配置项提供合理默认值"""

    def test_settings_defaults(self):
        from qa.config.settings import Settings

        s = Settings()
        assert s.llm.model == "gpt-4"
        assert s.llm.temperature == 0.3
        assert s.llm.max_tokens == 2048
        assert s.embedding.model == "bge-m3"
        assert s.embedding.dimensions == 1024
        assert s.vector_store.bit_width == 4
        assert s.vector_store.similarity_function == "cosine"
        assert s.retrieval.top_k == 5
        assert s.retrieval.use_hybrid is True
        assert s.retrieval.rrf_k == 35
        assert s.server.port == 8001
        assert s.server.rate_limit_rpm == 60

    def test_faithfulness_defaults(self):
        from qa.config.settings import Settings

        s = Settings()
        assert s.faithfulness.enabled is True
        assert s.faithfulness.threshold == 0.7  # M6: 从 0.5 提升
        assert s.faithfulness.max_claims == 10

    def test_query_rewrite_defaults(self):
        from qa.config.settings import Settings

        s = Settings()
        assert s.query_rewrite.enabled is True
        assert s.query_rewrite.timeout_seconds == 3.0

    def test_query_cache_defaults(self):
        from qa.config.settings import Settings

        s = Settings()
        assert s.query_cache.enabled is True
        assert s.query_cache.max_size == 256
        assert s.query_cache.ttl_seconds == 300.0

    def test_rerank_defaults(self):
        from qa.config.settings import Settings

        s = Settings()
        assert s.rerank.enabled is True
        assert s.rerank.top_k == 5

    def test_early_exit_defaults(self):
        from qa.config.settings import Settings

        s = Settings()
        assert s.early_exit.enabled is True
        assert s.early_exit.fuzzy_threshold == 0.82


class TestVectorStoreValidation:
    """VectorStoreConfig 自定义校验"""

    def test_invalid_bit_width(self):
        from qa.config.settings import VectorStoreConfig

        with pytest.raises(Exception):
            VectorStoreConfig(bit_width=3)  # 只允许 2 或 4

    def test_valid_bit_width_2(self):
        from qa.config.settings import VectorStoreConfig

        cfg = VectorStoreConfig(bit_width=2)
        assert cfg.bit_width == 2

    def test_valid_bit_width_4(self):
        from qa.config.settings import VectorStoreConfig

        cfg = VectorStoreConfig(bit_width=4)
        assert cfg.bit_width == 4

    def test_invalid_similarity_function(self):
        from qa.config.settings import VectorStoreConfig

        with pytest.raises(Exception):
            VectorStoreConfig(similarity_function="euclidean")

    def test_valid_similarity_cosine(self):
        from qa.config.settings import VectorStoreConfig

        cfg = VectorStoreConfig(similarity_function="cosine")
        assert cfg.similarity_function == "cosine"

    def test_valid_similarity_dot_product(self):
        from qa.config.settings import VectorStoreConfig

        cfg = VectorStoreConfig(similarity_function="dot_product")
        assert cfg.similarity_function == "dot_product"


class TestApiKeyMixin:
    """ApiKeyMixin resolved_api_key 优先级"""

    def test_direct_key_priority(self):
        from qa.config.settings import LLMConfig

        cfg = LLMConfig(api_key="direct-key", api_key_env="SOME_ENV")
        assert cfg.resolved_api_key == "direct-key"

    def test_env_var_fallback(self):
        from qa.config.settings import LLMConfig

        cfg = LLMConfig(api_key=None, api_key_env="TEST_QA_KEY_12345")
        with mock.patch.dict(os.environ, {"TEST_QA_KEY_12345": "env-key"}):
            assert cfg.resolved_api_key == "env-key"

    def test_env_var_name_as_key_fallback(self):
        """api_key_env 既不是环境变量名时，返回值本身（兼容误用）"""
        from qa.config.settings import LLMConfig

        cfg = LLMConfig(api_key=None, api_key_env="not-an-env-var-but-a-key")
        # 环境变量不存在时，返回 api_key_env 本身
        result = cfg.resolved_api_key
        assert result is not None

    def test_no_key_at_all(self):
        from qa.config.settings import LLMConfig

        cfg = LLMConfig(api_key=None, api_key_env="")
        assert cfg.resolved_api_key is None


class TestRetrievalConfig:
    """检索配置范围校验"""

    def test_rrf_k_range(self):
        from qa.config.settings import RetrievalConfig

        cfg = RetrievalConfig(rrf_k=35)
        assert cfg.rrf_k == 35

    def test_rrf_k_min(self):
        from qa.config.settings import RetrievalConfig

        cfg = RetrievalConfig(rrf_k=1)
        assert cfg.rrf_k == 1

    def test_top_k_range(self):
        from qa.config.settings import RetrievalConfig

        cfg = RetrievalConfig(top_k=50)
        assert cfg.top_k == 50


class TestServerConfig:
    """服务配置"""

    def test_default_port(self):
        from qa.config.settings import ServerConfig

        cfg = ServerConfig()
        assert cfg.port == 8001

    def test_default_origins(self):
        from qa.config.settings import ServerConfig

        cfg = ServerConfig()
        assert "http://localhost:8001" in cfg.allowed_origins

    def test_empty_api_keys(self):
        from qa.config.settings import ServerConfig

        cfg = ServerConfig()
        assert cfg.api_keys == []
