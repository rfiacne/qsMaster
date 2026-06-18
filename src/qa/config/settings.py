"""
配置管理模块 — Pydantic Settings 驱动。

配置加载优先级：
1. 环境变量覆写（所有字段均支持 QA_<SECTION>_<KEY> 环境变量）
2. ~/.qa/config.yaml 配置文件
3. 默认值
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    api_key: str | None = Field(
        default=None,
        description="API 密钥（直接填入 config.yaml，优先级最高）",
    )
    api_key_env: str = Field(
        default="INTERNAL_API_KEY",
        description="存储 API 密钥的环境变量名（如 INTERNAL_API_KEY）",
    )
    timeout_seconds: int = Field(default=30, ge=1, le=300, description="API 超时阈值（秒）")

    @property
    def resolved_api_key(self) -> str | None:
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
    api_key: str | None = Field(
        default=None,
        description="API 密钥（直接填入，优先级最高）",
    )
    api_key_env: str = Field(
        default="",
        description="存储 API 密钥的环境变量名（如 INTERNAL_API_KEY）",
    )
    timeout_seconds: int = Field(default=30, ge=1, le=300, description="API 超时阈值（秒）")

    @property
    def resolved_api_key(self) -> str | None:
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
    def validate_bit_width(self) -> VectorStoreConfig:
        if self.bit_width not in (2, 4):
            raise ValueError(f"bit_width 必须为 2 或 4，当前值: {self.bit_width}")
        return self

    @model_validator(mode="after")
    def validate_similarity(self) -> VectorStoreConfig:
        if self.similarity_function not in ("cosine", "dot_product"):
            raise ValueError(
                "similarity_function 必须为 'cosine' 或 'dot_product'，"
                f"当前值: {self.similarity_function}"
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
        default=0.01,
        ge=0.0,
        le=1.0,
        description="检索结果最低分数阈值（混合模式下 RRF 分数通常 0.005-0.05）",
    )
    use_hybrid: bool = Field(
        default=True,
        description="启用混合检索（BM25 关键词 + 向量语义）",
    )
    hybrid_vector_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="混合检索中向量检索权重，越高越依赖语义",
    )
    block_sizes: list[int] = Field(
        default=[500, 100],
        description="分层分块大小（字符数），[大块大小, 小块大小]",
    )
    rrf_k: int = Field(
        default=35,
        ge=1,
        le=200,
        description="RRF 融合常数，越小排名区分度越大（默认 35）",
    )


class IndexingConfig(BaseSettings):
    """索引配置"""

    model_config = SettingsConfigDict(env_prefix="qa_indexing_")

    batch_size: int = Field(default=100, ge=1, le=1000, description="批量索引大小")
    ocr_enabled: bool = Field(default=False, description="是否启用 OCR（扫描件）")
    ocr_backend: str = Field(
        default="auto",
        description="PDF 解析后端: auto | docling | opendataloader | paddle | none",
    )
    doc_timeout_seconds: int = Field(
        default=120,
        ge=10,
        le=600,
        description="单文档转换超时（秒，OCR 文档可能需要更长时间）",
    )


class RerankConfig(BaseSettings):
    """重排序（Reranker）配置"""

    model_config = SettingsConfigDict(env_prefix="qa_rerank_")

    enabled: bool = Field(
        default=True,
        description="启用 Reranker 精排（对混合检索结果二次打分，提升准确率）",
    )
    api_base_url: str = Field(
        default="",
        description="Reranker API 地址（空则复用 embedding.api_base_url）",
    )
    api_key: str | None = Field(
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
        default=5,
        ge=1,
        le=50,
        description="Reranker 重排后返回的结果数",
    )

    @property
    def resolved_api_key(self) -> str | None:
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


class EarlyExitConfig(BaseSettings):
    """Early Exit 配置"""

    model_config = SettingsConfigDict(env_prefix="qa_early_exit_")

    enabled: bool = Field(
        default=True,
        description="启用 Early Exit（标准答案库匹配优先）",
    )
    fuzzy_threshold: float = Field(
        default=0.82,
        ge=0.0,
        le=1.0,
        description="模糊匹配相似度阈值（低于此值不命中）",
    )
    store_path: str = Field(
        default="./data/standard_answers",
        description="标准答案库存储路径",
    )


class FaithfulnessConfig(BaseSettings):
    """Faithfulness 校验配置"""

    model_config = SettingsConfigDict(env_prefix="qa_faithfulness_")

    enabled: bool = Field(
        default=True,
        description="启用 Faithfulness 校验（LLM 生成后自动校验）",
    )
    mode: str = Field(
        default="llm",
        description="校验模式: llm | disabled",
    )
    threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="通过阈值：支撑比例 ≥ 此值算 PASS，< 此值算 FAIL（M6: 从 0.5 提升至 0.7）",
    )
    max_claims: int = Field(
        default=10,
        ge=1,
        le=30,
        description="最多校验的声明数（超长回答按段落分段聚合，不再硬截断）",
    )
    judge_model: str = Field(
        default="",
        description="校验用模型名（空则复用 LLM 主模型；设置独立模型避免自评偏差）",
    )
    judge_api_base_url: str = Field(
        default="",
        description=(
            "校验用独立 API 地址（空则复用 LLM 主地址；配合 judge_model 路由到独立评判端点）"
        ),
    )


class QueryRewriteConfig(BaseSettings):
    """查询改写配置"""

    model_config = SettingsConfigDict(env_prefix="qa_query_rewrite_")

    enabled: bool = Field(
        default=True,
        description="启用查询改写（术语归一化 + 多意图分解）",
    )
    model: str = Field(
        default="",
        description="改写用模型（空则复用 LLM 主模型）",
    )
    timeout_seconds: float = Field(
        default=3.0,
        ge=0.5,
        le=30.0,
        description="LLM 改写调用超时（秒）",
    )
    term_map_path: str = Field(
        default="./data/term_map.json",
        description="术语映射表路径（证券简称→全称）",
    )


class QueryCacheConfig(BaseSettings):
    """查询缓存配置"""

    model_config = SettingsConfigDict(env_prefix="qa_query_cache_")

    enabled: bool = Field(
        default=True,
        description="启用查询级 LRU 缓存",
    )
    max_size: int = Field(
        default=256,
        ge=16,
        le=65536,
        description="LRU 缓存最大条目数",
    )
    ttl_seconds: float = Field(
        default=300.0,
        ge=10.0,
        le=86400.0,
        description="缓存 TTL（秒，默认 5 分钟）",
    )


class PgConfig(BaseSettings):
    """PostgreSQL 全文检索配置"""

    model_config = SettingsConfigDict(env_prefix="qa_pg_")

    enabled: bool = Field(
        default=False,
        description="启用 PostgreSQL 全文检索（替代内存 BM25）",
    )
    host: str = Field(default="localhost", description="PG 主机")
    port: int = Field(default=5432, ge=1, le=65535, description="PG 端口")
    dbname: str = Field(default="qa", description="数据库名")
    user: str = Field(default="qa", description="用户名")
    password: str = Field(default="", description="密码")
    min_conn: int = Field(default=1, ge=0, le=10, description="最小连接数")
    max_conn: int = Field(default=5, ge=1, le=20, description="最大连接数")


class OTelConfig(BaseSettings):
    """OpenTelemetry 可观测性配置"""

    model_config = SettingsConfigDict(env_prefix="qa_otel_")

    enabled: bool = Field(
        default=False,
        description="启用 OpenTelemetry 链路追踪",
    )
    endpoint: str = Field(
        default="",
        description="OTLP HTTP 端点（如 http://otel-collector:4318）",
    )
    service_name: str = Field(
        default="securities-qa-agent",
        description="服务名称（用于 Trace 识别）",
    )


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
    early_exit: EarlyExitConfig = Field(default_factory=EarlyExitConfig)
    faithfulness: FaithfulnessConfig = Field(default_factory=FaithfulnessConfig)
    query_rewrite: QueryRewriteConfig = Field(default_factory=QueryRewriteConfig)
    query_cache: QueryCacheConfig = Field(default_factory=QueryCacheConfig)
    pg: PgConfig = Field(default_factory=PgConfig)
    otel: OTelConfig = Field(default_factory=OTelConfig)

    # 配置文件路径
    config_path: str = Field(
        default=str(Path.home() / ".qa" / "config.yaml"),
        description="配置文件路径",
    )

    @classmethod
    def load(cls, path: str | None = None) -> Settings:
        """从 YAML 配置文件加载设置，缺失字段使用默认值"""
        instance = cls()

        config_file = path or instance.config_path
        config_path = Path(config_file)

        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            # 递归合并各 section
            for section_key in (
                "llm",
                "embedding",
                "vector_store",
                "retrieval",
                "indexing",
                "server",
                "rerank",
                "early_exit",
                "faithfulness",
                "query_rewrite",
                "query_cache",
                "pg",
                "otel",
            ):
                if section_key in raw:
                    section_data = raw[section_key]
                    section_config = getattr(instance, section_key)
                    for k, v in section_data.items():
                        if hasattr(section_config, k) and v is not None:
                            setattr(section_config, k, v)

        return instance

    def as_dict(self) -> dict[str, Any]:
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
                "rrf_k": self.retrieval.rrf_k,
            },
            "indexing": {
                "batch_size": self.indexing.batch_size,
                "ocr_enabled": self.indexing.ocr_enabled,
                "ocr_backend": self.indexing.ocr_backend,
                "doc_timeout_seconds": self.indexing.doc_timeout_seconds,
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
            "early_exit": {
                "enabled": self.early_exit.enabled,
                "fuzzy_threshold": self.early_exit.fuzzy_threshold,
                "store_path": self.early_exit.store_path,
            },
            "faithfulness": {
                "enabled": self.faithfulness.enabled,
                "mode": self.faithfulness.mode,
                "threshold": self.faithfulness.threshold,
                "max_claims": self.faithfulness.max_claims,
                "judge_model": self.faithfulness.judge_model or "(复用 LLM)",
                "judge_api_base_url": self.faithfulness.judge_api_base_url or "(复用 LLM)",
            },
            "query_rewrite": {
                "enabled": self.query_rewrite.enabled,
                "model": self.query_rewrite.model or "(复用 LLM)",
                "timeout_seconds": self.query_rewrite.timeout_seconds,
                "term_map_path": self.query_rewrite.term_map_path,
            },
            "query_cache": {
                "enabled": self.query_cache.enabled,
                "max_size": self.query_cache.max_size,
                "ttl_seconds": self.query_cache.ttl_seconds,
            },
            "otel": {
                "enabled": self.otel.enabled,
                "endpoint": self.otel.endpoint or "(本地模式)",
                "service_name": self.otel.service_name,
            },
            "pg": {
                "enabled": self.pg.enabled,
                "host": self.pg.host,
                "port": self.pg.port,
                "dbname": self.pg.dbname,
            },
        }


# 模块级单例（延迟初始化）
_settings: Settings | None = None


def get_settings(path: str | None = None) -> Settings:
    """获取全局 Settings 单例"""
    global _settings
    if _settings is None:
        _settings = Settings.load(path)
    return _settings


def reload_settings(path: str | None = None) -> Settings:
    """重新加载配置"""
    global _settings
    _settings = Settings.load(path)
    return _settings
