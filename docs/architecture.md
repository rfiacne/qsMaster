# 系统架构

## 整体架构

Securities QA Agent 是一款面向证券清算领域的知识问答系统，采用 **RAG（检索增强生成）** 架构。系统核心为一条可配置的查询管道，依次经过多个组件处理，最终生成可信的回答。

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             用户入口层                                        │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────────────────────┐  │
│  │  CLI     │  │  Web 前端     │  │  REST API (FastAPI, 21 端点)          │  │
│  │ (Typer)  │  │ (index.html)  │  │  /api/v1/qa/*  /api/v1/ops/*         │  │
│  │ 11 命令   │  │ 7 管理页面     │  │  SSE 流式 / JSON / 上传 / 会话      │  │
│  └────┬─────┘  └──────┬───────┘  └──────────────────┬───────────────────┘  │
│       └───────────────┴───────────────┬─────────────┘                       │
└───────────────────────────────────────┼─────────────────────────────────────┘
                                        │
┌───────────────────────────────────────┼─────────────────────────────────────┐
│                             查询管道层                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │  QueryPipeline (querying.py)                                           ││
│  │                                                                        ││
│  │  1. EarlyExitMatcher ─── 标准答案库精确/模糊匹配，命中直接返回           ││
│  │  2. QueryCache ──────── LRU + TTL 缓存，相同问题命中直接返回            ││
│  │  3. QueryRewriter ──── 术语归一化（简称→全称）+ 多意图分解               ││
│  │  4. Embedding ──────── 远程 API / 本地模型 / 自动回退                   ││
│  │  5. HybridRetriever ── 向量检索 + BM25 关键词检索 + RRF 融合            ││
│  │  6. Reranker ───────── Cross-encoder 精排二次打分                       ││
│  │  7. AutoMerge ──────── 小块合并到大块，提供上下文窗口                   ││
│  │  8. LLM Generator ──── 流式/非流式生成，注入对话历史                     ││
│  │  9. Faithfulness ───── LLM-as-judge 声明级校验，FAIL→审核队列           ││
│  │                                                                        ││
│  │  输出: QueryResult { answer, sources[], faithfulness, session_id, ... } ││
│  └─────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
┌───────────────────────────────────────┼─────────────────────────────────────┐
│                             持久化层                                         │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ 向量索引      │  │ BM25 索引     │  │ 标准答案库    │  │ 审计日志        │  │
│  │ (turbovec)   │  │ (pickle)     │  │ (JSON)       │  │ (JSONL)        │  │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤  ├────────────────┤  │
│  │ store/       │  │ data/bm25/   │  │ data/        │  │ data/audit/    │  │
│  │ turbovec_    │  │ {version}.   │  │ standard_    │  │ audit.log      │  │
│  │ store.py     │  │ pkl         │  │ answers/     │  │ (仅追加)        │  │
│  └──────────────┘  └──────────────┘  └──────┬───────┘  └────────────────┘  │
│                                             │                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────┴───────┐  ┌────────────────┐  │
│  │ 会话存储      │  │ 审核队列      │  │ 术语映射表    │  │ 配置           │  │
│  │ (JSON)       │  │ (JSON)       │  │ (JSON)       │  │ (YAML/env)    │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 组件清单

### 管道组件 (`src/qa/pipelines/components/`)

| 文件 | 类/关键函数 | 角色 | 里程碑 |
|------|------------|------|--------|
| `early_exit.py` | `EarlyExitMatcher` | 标准答案库精确+模糊匹配，命中跳过 LLM | M2/M3 |
| `query_cache.py` | `QueryCache` | LRU+TTL 查询缓存，索引版本变更自动失效 | M6 |
| `query_rewriter.py` | `QueryRewriter` | 术语归一化（简称→全称）+ 多意图分解 | M6 |
| `embedder.py` | `embed_query`, `embed_texts` | 远程 API 嵌入，自动降级到本地模型 | M1 |
| `local_embedder.py` | `LocalEmbedder` | 本地嵌入模型（sentence-transformers）fallback | M2 |
| `hybrid_retriever.py` | `HybridRetriever` | 向量+BM25 双路检索 + RRF 融合 + 分数归一化 | M2/M6 |
| `bm25_index.py` | `BM25Index`, `load_or_build` | 全量 BM25Okapi 构建/持久化/版本化加载 | M6 |
| `reranker.py` | `RerankerComponent` | Cross-encoder 精排二次打分，失败回退 | M2 |
| `faithfulness.py` | `FaithfulnessEvaluator` | LLM-as-judge 声明级支撑校验 | M2/M6 |
| `session_store.py` | `SessionStore` | 持久化多轮会话（创建/保存/加载/列表） | M4 |
| `review_queue.py` | `ReviewWorkflow`, `ReviewStore` | 审核队列 + 三级标注 + 语义匹配自动入库 | M3 |
| `audit_logger.py` | `AuditStore` | 审计日志（JSONL，仅追加不可删除） | M4 |
| `tracing.py` | OpenTelemetry + Metrics | 链路追踪 + 请求计数/延迟/命中率 | M4 |
| `pg_retriever.py` | `PostgreSQLRetriever` | PostgreSQL 全文检索（pg_trgm + tsvector） | M2 |
| `timeout_utils.py` | `timeout_after` | 异步超时保护工具 | M1 |
| `hierarchical_store.py` | `HierarchicalDocumentStore` | 分层分块存储接口 | M1 |

### 管道编排 (`src/qa/pipelines/`)

| 文件 | 功能 |
|------|------|
| `querying.py` | `QueryPipeline` 主类：`run()` 非流式 + `run_stream()` 流式 |
| `indexing.py` | `IndexingPipeline`: 文档转换→分块→嵌入→写入向量库 |
| `factory.py` | `build_query_pipeline()`, `build_bm25_index()` 工厂函数 |
| `prompts.py` | LLM 提示模板（system prompt + faithfulness prompt） |

### API 层 (`src/qa/api/`)

| 文件 | 功能 |
|------|------|
| `server.py` | FastAPI 应用实例 + 前端静态文件挂载 + 直接启动入口 |
| `dependencies.py` | 单例管理 + `warmup()` 预热 + App 工厂 + 请求模型 |
| `middleware.py` | API 密钥鉴权 + 限流器 + 请求日志 |
| `routes/qa.py` | `/status`, `/ask`, `/ask/stream`, `/search` 路由 |
| `routes/sessions.py` | 会话列表/创建/删除路由 |
| `routes/answers.py` | 标准答案 CRUD + 别名 + 状态路由 |
| `routes/reviews.py` | 审核队列列表/标注/统计路由 |
| `routes/upload.py` | 文档上传+去重路由 |
| `routes/ops.py` | 健康检查/就绪探针/指标路由 |

### CLI 层 (`src/qa/cli/`)

| 文件 | 命令 | 功能 |
|------|------|------|
| `main.py` | `qa` | CLI 入口（Typer app） |
| `ask.py` | `qa ask` | 单次问答（支持 SSE 流式） |
| `chat.py` | `qa chat` | 持久化多轮会话（--session/--list/--resume） |
| `index.py` | `qa index` | 文档索引 |
| `answer.py` | `qa answer` | 标准答案 CRUD + 别名 + 启用/禁用 |
| `review.py` | `qa review` | 审核队列（list/show/approve/partial/reject/stats/archive） |
| `audit.py` | `qa audit` | 审计日志查询（时间/关键词/分页） |
| `compare.py` | `qa compare` | 混合检索对比测试 |
| `config.py` | `qa config` | 配置查看/管理 |
| `backup.py` | `qa backup` | 数据备份 |
| `term.py` | `qa term` | 术语映射表管理 |

## 数据流

### 索引流程

```
文档 (PDF/DOCX/XLSX/MD/HTML)
  → docling_converter.py 转换
  → 分块 (500/100 字符两级)
  → embedder.py 嵌入
  → turbovec_store.py 写入向量库
  → bm25_index.py 构建 BM25 索引 (增量时自动重建)
  → standard_answer_store 语义匹配入库 (可选)
```

### 查询流程

```
用户问题
  → EarlyExitMatcher (标准答案精确/模糊匹配)
    ├─ 命中 → 直接返回标准答案
    └─ 未命中
       → QueryCache (LRU 查缓存)
         ├─ 命中 → 直接返回缓存结果
         └─ 未命中
            → QueryRewriter (术语归一化 + 多意图分解)
            → Embedding (并行执行)
            → HybridRetriever (向量 + BM25 双路并行)
            → RRF 融合 + 分数归一化
            → Reranker (Cross-encoder 精排)
            → AutoMerge (小块→大块合并)
            → LLM Generator (注入对话历史，流式/非流式)
            → FaithfulnessEvaluator (声明级支撑校验)
              ├─ PASS → 返回回答
              ├─ PARTIAL → 返回回答 + 警告
              └─ FAIL → ReviewQueue 入库 + 拒答
            → QueryCache 写入
```

## 配置体系

配置加载优先级（从高到低）：
1. **环境变量**：`QA_<SECTION>_<KEY>` 覆写（如 `QA_LLM_MODEL`）
2. **配置文件**：`~/.qa/config.yaml`（YAML 格式）
3. **默认值**：Pydantic `Field(default=...)`

配置段对应 `src/qa/config/settings.py` 中的类：

| 配置段 | 设置类 | 环境变量前缀 |
|--------|--------|-------------|
| `llm` | `LLMConfig` | `QA_LLM_` |
| `embedding` | `EmbeddingConfig` | `QA_EMBEDDING_` |
| `vector_store` | `VectorStoreConfig` | `QA_VECTOR_STORE_` |
| `retrieval` | `RetrievalConfig` | `QA_RETRIEVAL_` |
| `indexing` | `IndexingConfig` | `QA_INDEXING_` |
| `server` | `ServerConfig` | `QA_SERVER_` |
| `rerank` | `RerankConfig` | `QA_RERANK_` |
| `early_exit` | `EarlyExitConfig` | `QA_EARLY_EXIT_` |
| `faithfulness` | `FaithfulnessConfig` | `QA_FAITHFULNESS_` |
| `query_rewrite` | `QueryRewriteConfig` | `QA_QUERY_REWRITE_` |
| `query_cache` | `QueryCacheConfig` | `QA_QUERY_CACHE_` |
| `pg` | `PgConfig` | `QA_PG_` |
| `otel` | `OTelConfig` | `QA_OTEL_` |

## API 路由图

```
/api/v1/qa/
├── GET    /status          # 知识库状态
├── POST   /ask             # 问答 (JSON)
├── POST   /ask/stream      # 流式问答 (SSE)
├── POST   /search          # 知识库检索
├── POST   /upload          # 上传文档
├── GET    /sessions        # 列出会话
├── POST   /sessions        # 创建会话
├── DELETE /sessions/{id}   # 删除会话
├── GET    /answers         # 标准答案列表
├── POST   /answers         # 新增标准答案
├── DELETE /answers/{id}    # 删除标准答案
├── PATCH  /answers/{id}/status   # 启用/禁用
├── POST   /answers/{id}/aliases  # 添加别名
├── GET    /reviews         # 审核队列
├── POST   /reviews/{id}/label    # 标注审核
├── GET    /reviews/stats   # 审核统计

/api/v1/ops/
├── GET    /health          # 健康检查
├── GET    /ready           # 就绪探针
├── GET    /metrics         # 可观测性指标
```

## 可观测性

| 能力 | 实现 |
|------|------|
| 结构化日志 | JSON 格式（ts/level/logger/msg），支持 request_id 传播 |
| OpenTelemetry 追踪 | 可选，通过 `otel.enabled` 配置，OTLP HTTP 导出 |
| 内置指标 | `InMemoryMetrics`: QPS/延迟百分位/Faithfulness 记录/缓存命中率 |
| 健康检查 | `/api/v1/ops/health` + `/api/v1/ops/ready` |
| 审计日志 | 所有写入操作记录到 `data/audit/audit.log`（JSONL，仅追加） |

## 关键技术栈

| 技术 | 用途 |
|------|------|
| Python 3.11+ | 运行时 |
| Haystack 2.x | RAG 管道框架 |
| FastAPI | REST API 服务 |
| Typer | CLI 命令行 |
| turbovec | 向量存储（本地量化索引） |
| rank-bm25 | BM25 关键词检索 |
| docling | 文档格式转换（PDF/DOCX/XLSX） |
| Pydantic / Pydantic-Settings | 配置管理 + 数据校验 |
| OpenTelemetry | 链路追踪（可选） |
| pytest | 测试框架 |
| ruff / mypy | 代码质量（lint/type check） |

## 性能目标

| 指标 | 目标 |
|------|------|
| 非流式 p95 | < 4s |
| 缓存命中 p95 | < 100ms |
| BM25 加载 p95 | < 50ms |
| 冷启动首请求 | < 2s |
