"""
配置管理模块 — Pydantic Settings 驱动。

配置加载优先级：
1. 环境变量覆写（所有字段均支持 QA_<SECTION>_<KEY> 环境变量）
2. ~/.qa/config.yaml 配置文件
3. 默认值
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    """LLM 配置"""

    model_config = SettingsConfigDict(env_prefix="qa_llm_")

    api_base_url: str = Field(
        default="http://localhost:8000/v1",
        description="内部 OpenAI 兼容 API 地址",
    )
    model: str = Field(default="gpt-4", description="LLM 模型名称")
    api_key: Optional[str] = Field(
        default=None,
        description="API 密钥（直接填入 config.yaml，优先级最高）",
    )
    api_key_env: str = Field(
        default="INTERNAL_API_KEY",
        description="存储 API 密钥的环境变量名（如 INTERNAL_API_KEY）",
    )
    timeout_seconds: int = Field(default=30, ge=1, le=300, description="API 超时阈值（秒）")

    @property
    def resolved_api_key(self) -> Optional[str]:
        """获取实际 API 密钥

        优先级: api_key 直接值 > api_key_env 环境变量名 > api_key_env 直接值（兼容误用）
        """
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            import os
            val = os.environ.get(self.api_key_env)
            if val:
                return val
            # api_key_env 的值可能本身就是 key（用户误把 key 填到了这里）
            return self.api_key_env
        return None


class EmbeddingConfig(BaseSettings):
    """嵌入模型配置"""

    model_config = SettingsConfigDict(env_prefix="qa_embedding_")

    backend: str = Field(
        default="auto",
        description="嵌入后端: auto(API优先,本地fallback) | api(仅远程) | local(仅本地)",
    )
    api_base_url: str = Field(
        default="http://localhost:8000/v1",
        description="嵌入 API 地址",
    )
    model: str = Field(default="bge-m3", description="嵌入模型名称")
    dimensions: int = Field(default=1024, ge=128, le=8192, description="向量维度")
    api_key: Optional[str] = Field(
        default=None,
        description="API 密钥（直接填入，优先级最高）",
    )
    api_key_env: str = Field(
        default="",
        description="存储 API 密钥的环境变量名（如 INTERNAL_API_KEY）",
    )
    timeout_seconds: int = Field(default=30, ge=1, le=300, description="API 超时阈值（秒）")

    @property
    def resolved_api_key(self) -> Optional[str]:
        """获取实际 API 密钥

        优先级: api_key 直接值 > api_key_env 环境变量名 > api_key_env 直接值（兼容误用）
        """
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            import os
            val = os.environ.get(self.api_key_env)
            if val:
                return val
            return self.api_key_env
        return None


class VectorStoreConfig(BaseSettings):
    """向量存储配置"""

    model_config = SettingsConfigDict(env_prefix="qa_vector_store_")

    type: str = Field(default="turbovec", description="向量库类型")
    bit_width: int = Field(default=4, ge=2, le=4, description="量化宽度（2 或 4）")
    similarity_function: str = Field(
        default="cosine",
        description="相似度函数（cosine 或 dot_product）",
    )
    persist_path: str = Field(
        default="./data/index",
        description="磁盘持久化路径",
    )

    @model_validator(mode="after")
    def validate_bit_width(self) -> "VectorStoreConfig":
        if self.bit_width not in (2, 4):
            raise ValueError(f"bit_width 必须为 2 或 4，当前值: {self.bit_width}")
        return self

    @model_validator(mode="after")
    def validate_similarity(self) -> "VectorStoreConfig":
        if self.similarity_function not in ("cosine", "dot_product"):
            raise ValueError(
                f"similarity_function 必须为 'cosine' 或 'dot_product'，当前值: {self.similarity_function}"
            )
        return self


class RetrievalConfig(BaseSettings):
    """检索配置"""

    model_config = SettingsConfigDict(env_prefix="qa_retrieval_")

    top_k: int = Field(default=5, ge=1, le=50, description="默认检索数量")
    auto_merge_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="AutoMergingRetriever 合并阈值"
    )
    min_score: float = Field(
        default=0.01, ge=0.0, le=1.0,
        description="检索结果最低分数阈值（混合模式下 RRF 分数通常 0.005-0.05）",
    )
    use_hybrid: bool = Field(
        default=True,
        description="启用混合检索（BM25 关键词 + 向量语义）",
    )
    hybrid_vector_weight: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="混合检索中向量检索权重，越高越依赖语义",
    )
    block_sizes: List[int] = Field(
        default=[500, 100],
        description="分层分块大小（字符数），[大块大小, 小块大小]",
    )


class IndexingConfig(BaseSettings):
    """索引配置"""

    model_config = SettingsConfigDict(env_prefix="qa_indexing_")

    batch_size: int = Field(default=100, ge=1, le=1000, description="批量索引大小")
    ocr_enabled: bool = Field(default=False, description="是否启用 OCR（扫描件）")


class RerankConfig(BaseSettings):
    """重排序（Reranker）配置"""

    model_config = SettingsConfigDict(env_prefix="qa_rerank_")

    enabled: bool = Field(
        default=False,
        description="启用 Reranker 精排（对混合检索结果二次打分，提升准确率）",
    )
    api_base_url: str = Field(
        default="",
        description="Reranker API 地址（空则复用 embedding.api_base_url）",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="Reranker API 密钥（空则复用 embedding 或 llm 的 key）",
    )
    api_key_env: str = Field(
        default="",
        description="Reranker API 密钥的环境变量名",
    )
    model: str = Field(
        default="Qwen/Qwen3-Reranker-4B",
        description="Reranker 模型名称",
    )
    top_k: int = Field(
        default=5, ge=1, le=50,
        description="Reranker 重排后返回的结果数",
    )

    @property
    def resolved_api_key(self) -> Optional[str]:
        """获取实际 API 密钥"""
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            import os
            val = os.environ.get(self.api_key_env)
            if val:
                return val
            return self.api_key_env
        return None


class ServerConfig(BaseSettings):
    """API 服务配置"""

    model_config = SettingsConfigDict(env_prefix="qa_server_")

    host: str = Field(default="127.0.0.1", description="监听地址（0.0.0.0 允许外网访问）")
    port: int = Field(default=8001, ge=1024, le=65535, description="监听端口")
    reload: bool = Field(default=False, description="开发模式：修改代码自动重启")


class Settings(BaseSettings):
    """应用全局配置"""

    model_config = SettingsConfigDict(env_prefix="qa_")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)

    # 配置文件路径
    config_path: str = Field(
        default=str(Path.home() / ".qa" / "config.yaml"),
        description="配置文件路径",
    )

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Settings":
        """从 YAML 配置文件加载设置，缺失字段使用默认值"""
        instance = cls()

        config_file = path or instance.config_path
        config_path = Path(config_file)

        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            # 递归合并各 section
            for section_key in ("llm", "embedding", "vector_store", "retrieval", "indexing", "server"):
                if section_key in raw:
                    section_data = raw[section_key]
                    section_config = getattr(instance, section_key)
                    for k, v in section_data.items():
                        if hasattr(section_config, k) and v is not None:
                            setattr(section_config, k, v)

        return instance

    def as_dict(self) -> Dict[str, Any]:
        """转字典（用于展示）"""
        return {
            "llm": {
                "api_base_url": self.llm.api_base_url,
                "model": self.llm.model,
                "api_key": "✅ 已配置" if self.llm.resolved_api_key else "❌ 未设置",
                "api_key_env": self.llm.api_key_env,
                "timeout_seconds": self.llm.timeout_seconds,
            },
            "embedding": {
                "backend": self.embedding.backend,
                "api_base_url": self.embedding.api_base_url,
                "model": self.embedding.model,
                "dimensions": self.embedding.dimensions,
                "api_key": "✅ 已配置" if self.embedding.resolved_api_key else "❌ 未设置",
                "api_key_env": self.embedding.api_key_env or "(未设置)",
                "timeout_seconds": self.embedding.timeout_seconds,
            },
            "vector_store": {
                "type": self.vector_store.type,
                "bit_width": self.vector_store.bit_width,
                "similarity_function": self.vector_store.similarity_function,
                "persist_path": self.vector_store.persist_path,
            },
            "retrieval": {
                "top_k": self.retrieval.top_k,
                "auto_merge_threshold": self.retrieval.auto_merge_threshold,
                "min_score": self.retrieval.min_score,
                "use_hybrid": self.retrieval.use_hybrid,
                "hybrid_vector_weight": self.retrieval.hybrid_vector_weight,
                "block_sizes": self.retrieval.block_sizes,
            },
            "indexing": {
                "batch_size": self.indexing.batch_size,
                "ocr_enabled": self.indexing.ocr_enabled,
            },
            "server": {
                "host": self.server.host,
                "port": self.server.port,
                "reload": self.server.reload,
            },
            "rerank": {
                "enabled": self.rerank.enabled,
                "api_base_url": self.rerank.api_base_url or "(复用 embedding)",
                "api_key": "✅ 已配置" if self.rerank.resolved_api_key else "❌ 未设置",
                "model": self.rerank.model,
                "top_k": self.rerank.top_k,
            },
        }


# 模块级单例（延迟初始化）
_settings: Optional[Settings] = None


def get_settings(path: Optional[str] = None) -> Settings:
    """获取全局 Settings 单例"""
    global _settings
    if _settings is None:
        _settings = Settings.load(path)
    return _settings


def reload_settings(path: Optional[str] = None) -> Settings:
    """重新加载配置"""
    global _settings
    _settings = Settings.load(path)
    return _settings
