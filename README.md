# Securities QA Agent

证券清算与技术知识问答系统。基于 Haystack 2.x + turbovec，覆盖 M1-M5 全链路：
**导入 → 混合检索 → Early Exit → LLM 生成 → 防幻觉校验 → 人工审核 → 审计留痕 → Web 管理**

## 架构

```
用户入口
  ├── CLI (10 个命令)
  ├── Web 前端 (7 个管理页面, SSE 流式打字机)
  └── REST API (17 个端点)

QueryPipeline
  ├── EarlyExitMatcher  ← 标准答案库 (JSON)
  │   ├─ 精确匹配 (字符串标准化)
  │   └─ 模糊匹配 (嵌入余弦相似度, 阈值可配)
  ├── QueryCache (LRU + TTL, Early Exit 之后检查)
  ├── QueryRewriter (术语归一化 + 多意图分解, 嵌入前)
  ├── Embedding (远程 API / 本地模型 / 自动回退)
  ├── HybridRetriever (向量检索 + 全量持久化 BM25/PG RRF 融合)
  ├── Reranker (Cross-encoder 精排)
  ├── LLM Generator (流式/非流式, 注入对话历史)
  ├── QueryRewriter (术语归一化 + 多意图分解)
  ├── HybridRetriever (向量检索 + 全量持久化 BM25/PG RRF 融合)
  ├── Reranker (Cross-encoder 精排)
  ├── LLM Generator (流式/非流式, 注入对话历史)
  └── FaithfulnessEvaluator (LLM-as-judge 声明级校验)
       └── FAIL → ReviewQueue → 人工标注 → 语义匹配自动入库

持久化层
  ├── 标准答案库 (JSON, 含别名/状态/审计)
  ├── 审核队列 (JSON, 三级标注/归档)
  ├── 会话 (JSON, 持久化多轮对话)
  ├── 审计日志 (JSONL, 仅追加不可删除)
  └── 向量索引 (turbovec)

可观测性
  └── OpenTelemetry Trace + InMemoryMetrics (QPS/延迟/命中率)
```

## 快速开始

```bash
# 安装核心依赖
pip install haystack-ai typer rich pyyaml openai pydantic pydantic-settings
pip install python-docx markdown beautifulsoup4 lxml openpyxl xlrd pypdf rank-bm25

# 初始化配置
cp config.example.yaml ~/.qa/config.yaml
# 编辑 ~/.qa/config.yaml 填入 API 地址
set INTERNAL_API_KEY="your-api-key"

# 导入文档
python -m qa.cli.main index ./docs --source CSDC --category clearing_rule --effective-date 2026-06-01

# 单次问答
python -m qa.cli.main ask "沪深交易所 T+1 清算流程是什么？"

# 交互式会话（持久化，可恢复）
python -m qa.cli.main chat
python -m qa.cli.main chat --session <id>     # 恢复会话
python -m qa.cli.main chat --list             # 列出会话
python -m qa.cli.main chat --resume           # 恢复最近

# 启动 Web 服务（前端 + API 一体）
python -m qa.api.server
# 浏览器打开 http://127.0.0.1:8001
```

## CLI 命令

| 命令 | 功能 | 里程碑 |
|------|------|--------|
| `qa ask` | 单次问答（支持流式 SSE） | M1 |
| `qa chat --session/--list/--resume` | 持久化多轮会话 | M1→M4 |
| `qa index` | 文档索引 | M1 |
| `qa status` | 知识库状态 | M1 |
| `qa remove` | 删除文档 | M1 |
| `qa config show` | 查看配置 | M1 |
| `qa answer add/list/edit/remove/disable/enable/alias` | 标准答案管理 | M2→M3 |
| `qa compare` | 混合检索对比测试 | M2 |
| `qa review list/show/approve/partial/reject/stats/archive` | 审核队列 | M3 |
| `qa audit list/show/stats` | 审计日志查询 | M4 |

## REST API（17 端点）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/qa/ask` | POST | 问答（JSON） |
| `/api/v1/qa/ask/stream` | POST | 流式问答（SSE） |
| `/api/v1/qa/search` | POST | 知识库检索 |
| `/api/v1/qa/upload` | POST | 上传文档 |
| `/api/v1/qa/status` | GET | 知识库状态 |
| `/api/v1/qa/sessions` | GET | 列出会话 |
| `/api/v1/qa/sessions/{id}` | DELETE | 删除会话 |
| `/api/v1/qa/answers` | GET/POST | 标准答案列表/新增 |
| `/api/v1/qa/answers/{id}` | DELETE | 删除标准答案 |
| `/api/v1/qa/answers/{id}/status` | PATCH | 启用/禁用 |
| `/api/v1/qa/answers/{id}/aliases` | POST | 添加别名 |
| `/api/v1/qa/reviews` | GET | 审核队列 |
| `/api/v1/qa/reviews/{id}/label` | POST | 标注审核 |
| `/api/v1/qa/reviews/stats` | GET | 审核统计 |
| `/api/v1/qa/metrics` | GET | 可观测性指标 |
| `/api/v1/qa/health` | GET | 健康检查 |

## 测试

```bash
# 单元测试（mock, 无需外部依赖）
python -m pytest tests/unit/ -v

# 集成测试（需 testfile 目录下的真实文档）
python -m pytest tests/integration/ -v

# 全部
python -m pytest tests/unit/ && python -m pytest tests/integration/
```

**266 单元 + 33 集成 = 299 测试** (全部通过)

## 配置

完整配置项见 [`config.example.yaml`](./config.example.yaml)，支持环境变量覆写（`QA_<SECTION>_<KEY>`）。

| 配置段 | 说明 | 环境变量前缀 |
|--------|------|-------------|
| `llm` | LLM API | `QA_LLM_` |
| `embedding` | 嵌入模型 | `QA_EMBEDDING_` |
| `vector_store` | 向量库 (turbovec) | `QA_VECTOR_STORE_` |
| `retrieval` | 检索参数 | `QA_RETRIEVAL_` |
| `indexing` | 索引参数 | `QA_INDEXING_` |
| `server` | API 服务 | `QA_SERVER_` |
| `rerank` | Reranker 精排 | `QA_RERANK_` |
| `early_exit` | 标准答案匹配 | `QA_EARLY_EXIT_` |
| `faithfulness` | 防幻觉校验 | `QA_FAITHFULNESS_` |
| `query_rewrite` | 查询改写（M6） | `QA_QUERY_REWRITE_` |
| `query_cache` | 查询缓存（M6） | `QA_QUERY_CACHE_` |
| `pg` | PostgreSQL 全文检索 | `QA_PG_` |
| `otel` | OpenTelemetry | `QA_OTEL_` |

## 依赖安装

```bash
pip install -e ".[dev]"         # 核心 + 开发 (pytest/ruff/mypy)
pip install -e ".[ocr]"         # OCR (PaddleOCR)
pip install -e ".[pdf]"         # PDF 增强 (OpenDataLoader, 需 Java 11+)
pip install -e ".[local]"       # 本地嵌入 (sentence-transformers + torch)
```

或使用 requirements 文件：`requirements.txt` / `requirements-all.txt` / `requirements-ocr.txt` / `requirements-pdf.txt` / `requirements-local.txt`

## 项目结构

```
src/qa/
├── cli/              # CLI 命令 (ask, chat, index, answer, compare, review, audit)
│   ├── ask.py        #   单次问答
│   ├── chat.py       #   持久化会话 (--session/--list/--resume)
│   ├── answer.py     #   标准答案 CRUD + 别名 + 状态
│   ├── review.py     #   审核队列 (标注/统计/归档)
│   ├── audit.py      #   审计日志 (时间/关键词/分页)
│   ├── compare.py    #   混合检索对比测试
│   ├── config.py     #   配置管理
│   └── index.py      #   文档索引
├── pipelines/
│   ├── querying.py   #   QueryPipeline (run + run_stream)
│   ├── indexing.py   #   IndexingPipeline (带超时保护)
│   └── components/   #   自定义组件
│       ├── early_exit.py      # 标准答案库 + 匹配器
│       ├── faithfulness.py    # LLM-as-judge 防幻觉
│       ├── hybrid_retriever.py # 向量+BM25/PG RRF 融合
│       ├── bm25_index.py      # M6: 全量 BM25 索引构建/持久化/版本化加载
│       ├── query_rewriter.py  # M6: 术语归一化 + 多意图分解
│       ├── query_cache.py     # M6: LRU 查询缓存
│       ├── reranker.py        # Cross-encoder 精排
│       ├── review_queue.py    # 审核队列 + 语义匹配入库
│       ├── session_store.py   # 持久化会话
│       ├── audit_logger.py    # 审计日志 (JSONL)
│       ├── tracing.py         # OpenTelemetry + Metrics
│       ├── pg_retriever.py    # PostgreSQL 全文检索
│       └── timeout_utils.py   # 超时保护工具
├── api/server.py     # FastAPI 服务 (17 端点, SSE 流式)
├── stores/           # turbovec 封装
├── config/settings.py# Pydantic 配置 (11 段)
└── converters/       # 文档转换路由 (PDF/DOCX/XLSX/MD/HTML)
frontend/index.html   # Web 前端 (7 页面, SSE 打字机)
tests/
├── unit/             # 251 个单元测试 (mock, 无需外部依赖)
└── integration/      # 32 个集成测试 (使用 testfile 真实文档)
```

## 里程碑

| 里程碑 | 描述 | 状态 |
|--------|------|------|
| M1 | 核心 RAG 闭环（文档导入 → 向量检索 → LLM 生成） | ✅ |
| M2 | 混合检索 + Early Exit 标准答案库 + Faithfulness 防幻觉 | ✅ |
| M3 | 标准答案别名/状态/审计 + 审核工作流 + 语义匹配入库 | ✅ |
| M4 | 持久化多轮会话 + 审计日志 + OpenTelemetry 可观测性 | ✅ |
| M5 | Web 前端管理 + SSE 流式 + 管理 API | ✅ |
| M6 | 相关性增强（BM25 全量化/Reranker 默认开/查询改写/防幻觉强化） + 前端美观性 + 性能优化（缓存/预热/并行/批量嵌入） | ✅ |
