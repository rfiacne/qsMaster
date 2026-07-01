# 配置参考

## 概览

系统配置采用 **YAML 配置文件 + 环境变量覆写 + 默认值** 三层结构，由 Pydantic-Settings 驱动。

配置加载优先级（高→低）：
1. **环境变量** `QA_<SECTION>_<KEY>`（如 `QA_LLM_MODEL=gpt-4`）
2. **配置文件** `~/.qa/config.yaml`（或通过 `config_path` 指定路径）
3. **代码默认值**（Pydantic `Field(default=...)`）

## 配置文件位置

默认路径：`~/.qa/config.yaml`

可通过以下方式指定：
- 设置 `config_path` 字段（不推荐）
- 调用 `get_settings(path="/your/path/config.yaml")`

## 配置段列表

| 配置段 | 设置类 | 环境变量前缀 | 里程碑 | 热重载 |
|--------|--------|-------------|--------|--------|
| `llm` | `LLMConfig` | `QA_LLM_` | M1 | 否 |
| `embedding` | `EmbeddingConfig` | `QA_EMBEDDING_` | M1 | 否 |
| `vector_store` | `VectorStoreConfig` | `QA_VECTOR_STORE_` | M1 | 否 |
| `retrieval` | `RetrievalConfig` | `QA_RETRIEVAL_` | M1 | 是 |
| `indexing` | `IndexingConfig` | `QA_INDEXING_` | M1 | 否 |
| `server` | `ServerConfig` | `QA_SERVER_` | M4 | 否 |
| `rerank` | `RerankConfig` | `QA_RERANK_` | M2 | 是 |
| `early_exit` | `EarlyExitConfig` | `QA_EARLY_EXIT_` | M2 | 是 |
| `faithfulness` | `FaithfulnessConfig` | `QA_FAITHFULNESS_` | M2/M6 | 是 |
| `query_rewrite` | `QueryRewriteConfig` | `QA_QUERY_REWRITE_` | M6 | 是 |
| `query_cache` | `QueryCacheConfig` | `QA_QUERY_CACHE_` | M6 | 是 |
| `pg` | `PgConfig` | `QA_PG_` | M2 | 否 |
| `otel` | `OTelConfig` | `QA_OTEL_` | M4 | 否 |

## 详细配置项

### llm — LLM 配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `api_base_url` | string | `http://localhost:8000/v1` | `QA_LLM_API_BASE_URL` | OpenAI 兼容 API 地址 |
| `model` | string | `gpt-4` | `QA_LLM_MODEL` | 模型名称 |
| `api_key` | string | null | `QA_LLM_API_KEY` | API 密钥（直接值，优先级最高） |
| `api_key_env` | string | `INTERNAL_API_KEY` | `QA_LLM_API_KEY_ENV` | 存密钥的环境变量名 |
| `timeout_seconds` | int | 30 | `QA_LLM_TIMEOUT_SECONDS` | API 超时（秒） |
| `temperature` | float | 0.3 | `QA_LLM_TEMPERATURE` | 生成温度 |
| `max_tokens` | int | 2048 | `QA_LLM_MAX_TOKENS` | 最大生成 token 数 |

API 密钥解析优先级：`api_key` 直接值 → `api_key_env` 指向的环境变量 → `api_key_env` 直接值（兼容误用）。

### embedding — 嵌入模型配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `backend` | string | `auto` | `QA_EMBEDDING_BACKEND` | `auto`(远程→本地fallback) / `api`(仅远程) / `local`(仅本地) |
| `api_base_url` | string | `http://localhost:8000/v1` | `QA_EMBEDDING_API_BASE_URL` | 嵌入 API 地址 |
| `model` | string | `bge-m3` | `QA_EMBEDDING_MODEL` | 嵌入模型名称 |
| `dimensions` | int | 1024 | `QA_EMBEDDING_DIMENSIONS` | 向量维度 |
| `api_key` | string | null | `QA_EMBEDDING_API_KEY` | API 密钥 |
| `api_key_env` | string | `` | `QA_EMBEDDING_API_KEY_ENV` | 存密钥的环境变量名 |
| `timeout_seconds` | int | 30 | `QA_EMBEDDING_TIMEOUT_SECONDS` | API 超时（秒） |

### vector_store — 向量存储配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `type` | string | `turbovec` | `QA_VECTOR_STORE_TYPE` | 向量库类型 |
| `bit_width` | int | 4 | `QA_VECTOR_STORE_BIT_WIDTH` | 量化宽度（2 或 4） |
| `similarity_function` | string | `cosine` | `QA_VECTOR_STORE_SIMILARITY_FUNCTION` | 相似度函数 |
| `persist_path` | string | `./data/index` | `QA_VECTOR_STORE_PERSIST_PATH` | 磁盘持久化路径 |

### retrieval — 检索配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `top_k` | int | 5 | `QA_RETRIEVAL_TOP_K` | 默认检索数量 |
| `auto_merge_threshold` | float | 0.5 | `QA_RETRIEVAL_AUTO_MERGE_THRESHOLD` | AutoMerge 合并阈值 |
| `min_score` | float | 0.01 | `QA_RETRIEVAL_MIN_SCORE` | 检索结果最低分数（RRF 分数通常 0.005-0.05） |
| `use_hybrid` | bool | true | `QA_RETRIEVAL_USE_HYBRID` | 启用混合检索（BM25 + 向量） |
| `hybrid_vector_weight` | float | 0.5 | `QA_RETRIEVAL_HYBRID_VECTOR_WEIGHT` | 向量检索权重（0~1） |
| `block_sizes` | int[] | [500, 100] | `QA_RETRIEVAL_BLOCK_SIZES` | 分层分块大小（字符） |
| `rrf_k` | int | 35 | `QA_RETRIEVAL_RRF_K` | RRF 融合常数（越小区分度越大，M6） |

### indexing — 索引配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `batch_size` | int | 100 | `QA_INDEXING_BATCH_SIZE` | 批量索引大小 |
| `ocr_enabled` | bool | false | `QA_INDEXING_OCR_ENABLED` | 启用 OCR（扫描件） |
| `ocr_backend` | string | `auto` | `QA_INDEXING_OCR_BACKEND` | 解析后端：`auto` / `docling` / `opendataloader` / `paddle` / `none` |
| `doc_timeout_seconds` | int | 120 | `QA_INDEXING_DOC_TIMEOUT_SECONDS` | 单文档转换超时（秒） |

### server — API 服务配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `host` | string | `127.0.0.1` | `QA_SERVER_HOST` | 监听地址 |
| `port` | int | 8001 | `QA_SERVER_PORT` | 监听端口 |
| `reload` | bool | false | `QA_SERVER_RELOAD` | 开发模式自动重启 |
| `api_keys` | string[] | [] | `QA_SERVER_API_KEYS` | API 密钥列表（逗号分隔，空=不鉴权） |
| `rate_limit_rpm` | int | 60 | `QA_SERVER_RATE_LIMIT_RPM` | 每分钟请求限制（per key，0=不限） |
| `allowed_origins` | string[] | ["http://localhost:8001"] | `QA_SERVER_ALLOWED_ORIGINS` | CORS 允许来源 |

**注意**：`api_keys` 和 `allowed_origins` 为数组字段，环境变量支持 CSV 格式（逗号分隔）和 JSON 格式。

### rerank — Reranker 精排配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `enabled` | bool | true | `QA_RERANK_ENABLED` | 启用 Reranker（M6 默认开） |
| `api_base_url` | string | `` | `QA_RERANK_API_BASE_URL` | API 地址（空则复用 embedding） |
| `api_key` | string | null | `QA_RERANK_API_KEY` | API 密钥 |
| `api_key_env` | string | `` | `QA_RERANK_API_KEY_ENV` | 存密钥的环境变量名 |
| `model` | string | `Qwen/Qwen3-Reranker-4B` | `QA_RERANK_MODEL` | 模型名称 |
| `top_k` | int | 5 | `QA_RERANK_TOP_K` | 重排后返回数 |
| `timeout_seconds` | int | 30 | `QA_RERANK_TIMEOUT_SECONDS` | API 超时（秒） |

### early_exit — 标准答案匹配配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `enabled` | bool | true | `QA_EARLY_EXIT_ENABLED` | 启用 Early Exit |
| `fuzzy_threshold` | float | 0.82 | `QA_EARLY_EXIT_FUZZY_THRESHOLD` | 模糊匹配相似度阈值 |
| `store_path` | string | `./data/standard_answers` | `QA_EARLY_EXIT_STORE_PATH` | 标准答案库路径 |

### faithfulness — 防幻觉校验配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `enabled` | bool | true | `QA_FAITHFULNESS_ENABLED` | 启用 Faithfulness 校验 |
| `mode` | string | `llm` | `QA_FAITHFULNESS_MODE` | 校验模式：`llm` / `disabled` |
| `threshold` | float | 0.7 | `QA_FAITHFULNESS_THRESHOLD` | 通过阈值（M6: 0.5→0.7） |
| `max_claims` | int | 10 | `QA_FAITHFULNESS_MAX_CLAIMS` | 最多校验声明数 |
| `judge_model` | string | `` | `QA_FAITHFULNESS_JUDGE_MODEL` | 独立评判模型（空则复用 LLM） |
| `judge_api_base_url` | string | `` | `QA_FAITHFULNESS_JUDGE_API_BASE_URL` | 独立评判 API 地址 |

### query_rewrite — 查询改写配置（M6）

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `enabled` | bool | true | `QA_QUERY_REWRITE_ENABLED` | 启用查询改写 |
| `model` | string | `` | `QA_QUERY_REWRITE_MODEL` | 改写用模型（空则复用 LLM） |
| `timeout_seconds` | float | 3.0 | `QA_QUERY_REWRITE_TIMEOUT_SECONDS` | LLM 改写超时（秒） |
| `term_map_path` | string | `./data/term_map.json` | `QA_QUERY_REWRITE_TERM_MAP_PATH` | 术语映射表路径 |

### query_cache — 查询缓存配置（M6）

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `enabled` | bool | true | `QA_QUERY_CACHE_ENABLED` | 启用查询缓存 |
| `max_size` | int | 256 | `QA_QUERY_CACHE_MAX_SIZE` | LRU 缓存最大条目数 |
| `ttl_seconds` | float | 300 | `QA_QUERY_CACHE_TTL_SECONDS` | 缓存 TTL（秒） |

### pg — PostgreSQL 全文检索配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `enabled` | bool | false | `QA_PG_ENABLED` | 启用 PG 全文检索 |
| `host` | string | `localhost` | `QA_PG_HOST` | 主机 |
| `port` | int | 5432 | `QA_PG_PORT` | 端口 |
| `dbname` | string | `qa` | `QA_PG_DBNAME` | 数据库名 |
| `user` | string | `qa` | `QA_PG_USER` | 用户名 |
| `password` | string | `` | `QA_PG_PASSWORD` | 密码（建议环境变量） |
| `min_conn` | int | 1 | `QA_PG_MIN_CONN` | 最小连接数 |
| `max_conn` | int | 5 | `QA_PG_MAX_CONN` | 最大连接数 |

### otel — OpenTelemetry 可观测性配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
|------|------|--------|---------|------|
| `enabled` | bool | false | `QA_OTEL_ENABLED` | 启用链路追踪 |
| `endpoint` | string | `` | `QA_OTEL_ENDPOINT` | OTLP HTTP 端点 |
| `service_name` | string | `securities-qa-agent` | `QA_OTEL_SERVICE_NAME` | 服务名称 |

## 热重载说明

以下配置段修改后**无需重启服务**（通过 `reload_settings()` 重新加载）：
- `retrieval`（检索参数）
- `rerank`（精排开关）
- `early_exit`（标准答案匹配阈值）
- `faithfulness`（校验阈值/模型）
- `query_rewrite`（改写开关/模型）
- `query_cache`（缓存参数）

以下配置段修改后**需要重启服务**：
- `llm`（LLM API 地址）
- `embedding`（嵌入模型/后端）
- `vector_store`（向量库路径/量化参数）
- `indexing`（索引参数）
- `server`（监听地址/端口/鉴权）
- `pg`（PostgreSQL 连接参数）
- `otel`（可观测性开关）

## 环境变量配置示例

```bash
# LLM
QA_LLM_API_BASE_URL=http://your-llm:8000/v1
QA_LLM_API_KEY=sk-your-key-here
QA_LLM_MODEL=gpt-4

# Embedding
QA_EMBEDDING_API_BASE_URL=http://your-llm:8000/v1
QA_EMBEDDING_MODEL=bge-m3

# Server
QA_SERVER_HOST=0.0.0.0
QA_SERVER_PORT=8001
QA_SERVER_API_KEYS=sk-key-1,sk-key-2
QA_SERVER_RATE_LIMIT_RPM=100
QA_SERVER_ALLOWED_ORIGINS=https://your-domain.com,http://localhost:8001
```

## 配置迁移指南

### 从 M2 到 M6

| 变更 | 旧值 | 新值 |
|------|------|------|
| `rerank.enabled` | `false`（默认） | `true`（默认） |
| `faithfulness.threshold` | 0.5 | 0.7 |
| `retrieval.rrf_k` | 60（默认） | 35（默认） |
| 新增 `query_rewrite` 块 | — | 见 config.example.yaml |
| 新增 `query_cache` 块 | — | 见 config.example.yaml |
| `sources[].score` 语义 | 向量余弦 | 归一化 RRF 分数 |

所有新增配置项有默认值，无需改动已有 `config.yaml`。
