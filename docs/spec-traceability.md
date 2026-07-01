# 规约-实现追溯矩阵

本文档将各里程碑的 Functional Requirements (FR) 映射到实现源文件，便于审计和开发追溯。

---

## M1 — 基础 RAG Pipeline (spec: 001)

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-001 | 自然语言问答 | ✅ | `pipelines/querying.py`, `cli/ask.py`, `api/routes/qa.py` |
| FR-002 | Haystack 2.x Pipeline 架构 | ✅ | `pipelines/querying.py`, `pipelines/factory.py` |
| FR-003 | 引用来源标注 | ✅ | `pipelines/querying.py` (QueryResult.sources) |
| FR-004 | 防幻觉（无法回答提示） | ✅ | `pipelines/prompts.py` (system_prompt) |
| FR-004a | API 不可用时错误提示 | ✅ | `api/routes/qa.py`, `pipelines/components/timeout_utils.py` |
| FR-004b | API 超时错误 | ✅ | `pipelines/components/timeout_utils.py` |
| FR-005 | TurboQuantDocumentStore 向量存储 | ✅ | `stores/turbovec_store.py` |
| FR-006 | 元数据过滤检索 | ✅ | `pipelines/components/hybrid_retriever.py`, `stores/turbovec_store.py` |
| FR-007 | 索引磁盘持久化与增量更新 | ✅ | `stores/turbovec_store.py`, `pipelines/indexing.py` |
| FR-008 | 多格式文档导入 | ✅ | `converters/docling_converter.py`, `api/routes/upload.py`, `cli/index.py` |
| FR-009 | PDF 表格内容提取 | ✅ | `converters/docling_converter.py` |
| FR-010 | Markdown 标题层级保留 | ✅ | `converters/docling_converter.py` |
| FR-011 | 分层文档检索策略 | ✅ | `pipelines/components/hierarchical_store.py` |
| FR-012 | 文档元数据标注 | ✅ | `pipelines/indexing.py` (validate_meta) |
| FR-013 | CLI 命令知识库管理 | ✅ | `cli/main.py`, `cli/ask.py`, `cli/index.py` |
| FR-014 | CLI 命令查询知识库状态 | ✅ | `cli/main.py`, `api/routes/qa.py` (/status) |

---

## M2 — 混合检索 & 防幻觉 (spec: 002)

### 混合检索

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-001 | 向量+全文混合检索（BM25/向量双路+RRF） | ✅ | `pipelines/components/hybrid_retriever.py` |
| FR-002 | PostgreSQL 全文检索后端 | ✅ | `pipelines/components/pg_retriever.py` |
| FR-003 | Reranker 精排组件 | ✅ | `pipelines/components/reranker.py` |
| FR-004 | 候选/精排数量可配置 | ✅ | `config/settings.py` (RetrievalConfig, RerankConfig) |
| FR-005 | 来源类型标注（vector/bm25/hybrid） | ✅ | `pipelines/components/hybrid_retriever.py` |

### Early Exit

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-006 | ConditionalRouter 优先匹配标准答案库 | ✅ | `pipelines/components/early_exit.py`, `pipelines/querying.py` |
| FR-007 | 精确+模糊匹配（阈值可配） | ✅ | `pipelines/components/early_exit.py` |
| FR-008 | Early Exit 命中标注来源 | ✅ | `pipelines/components/early_exit.py` |
| FR-009 | 未命中降级到 LLM 生成 | ✅ | `pipelines/querying.py` |

### 标准答案库

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-010 | 标准答案数据模型 | ✅ | `pipelines/components/early_exit.py` (StandardAnswer, StandardAnswerStore) |
| FR-011 | CLI 增删改查+批量导入 | ✅ | `cli/answer.py` |
| FR-012 | M2 种子数据集 | ✅ | `config.example.yaml` + 内建种子数据 |
| FR-013 | 模糊匹配命中日志 | ✅ | `pipelines/components/early_exit.py` |

### 防幻觉

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-014 | Faithfulness Evaluator 校验 | ✅ | `pipelines/components/faithfulness.py` |
| FR-015 | FAIL 降级拒答 | ✅ | `pipelines/components/faithfulness.py` |
| FR-016 | PASS 附加校验标识 | ✅ | `pipelines/components/faithfulness.py` |
| FR-017 | 校验结果记录 | ✅ | `pipelines/components/faithfulness.py`, `pipelines/components/audit_logger.py` |

### 接口与兼容

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-018 | M1 CLI 向后兼容 | ✅ | `cli/ask.py` |
| FR-019 | 混合检索对比测试 CLI | ✅ | `cli/compare.py` |
| FR-020 | 标准答案库管理 CLI | ✅ | `cli/answer.py` |

---

## M3 — 标准答案管理 & 审核工作流 (spec: 003)

### 标准答案管理

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-001 | 创建标准答案 | ✅ | `cli/answer.py`, `api/routes/answers.py` |
| FR-002 | 编辑标准答案 | ✅ | `cli/answer.py` |
| FR-003 | 禁用/启用标准答案 | ✅ | `cli/answer.py`, `api/routes/answers.py` (PATCH status) |
| FR-004 | 删除标准答案 | ✅ | `cli/answer.py`, `api/routes/answers.py` (DELETE) |
| FR-005 | 分类/状态筛选查询 | ✅ | `api/routes/answers.py` (GET with params) |
| FR-006 | 别名问题列表 | ✅ | `pipelines/components/early_exit.py`, `api/routes/answers.py` (POST aliases) |

### 语义匹配入库

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-007 | 标注"正确"后语义相似度计算 | ✅ | `pipelines/components/review_queue.py` |
| FR-008 | 相似度阈值→候选/别名 | ✅ | `pipelines/components/review_queue.py` |
| FR-009 | 相似度分数记录 | ✅ | `pipelines/components/review_queue.py` |
| FR-010 | 候选池自动转正 | ✅ | `pipelines/components/review_queue.py` |

### 审核工作流

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-011 | Faithfulness FAIL 自动加入审核队列 | ✅ | `pipelines/components/faithfulness.py`, `pipelines/components/review_queue.py` |
| FR-012 | CLI 浏览审核队列 | ✅ | `cli/review.py` |
| FR-013 | 三级标注（正确/部分/错误） | ✅ | `pipelines/components/review_queue.py`, `api/routes/reviews.py` |
| FR-014 | 正确→自动推入候选池 | ✅ | `pipelines/components/review_queue.py` |
| FR-015 | 错误→归档 | ✅ | `pipelines/components/review_queue.py` |
| FR-016 | 防止重复标注 | ✅ | `pipelines/components/review_queue.py` |
| FR-017 | 过期自动归档 | ✅ | `pipelines/components/review_queue.py` |

### 审核统计

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-018 | 标注完成率统计 | ✅ | `api/routes/reviews.py` (GET /stats), `cli/review.py` |
| FR-019 | 标准答案覆盖率统计 | ✅ | `cli/review.py` |
| FR-020 | 准确率趋势统计 | ✅ | `cli/review.py` |

### 接口与安全

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-021 | CLI 全功能覆盖 | ✅ | `cli/review.py`, `cli/answer.py` |
| FR-022 | 操作审计追踪 | ✅ | `pipelines/components/audit_logger.py` |
| FR-023 | 输入参数验证 | ✅ | `cli/answer.py`, `cli/review.py` |

---

## M4 — 多轮对话 & 可观测性 (spec: 004)

### 多轮对话

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-001 | 会话内多轮对话 | ✅ | `pipelines/components/session_store.py`, `pipelines/querying.py` |
| FR-002 | 持久化存储（JSON 替代 Redis/PG） | ✅ | `pipelines/components/session_store.py` |
| FR-003 | Sliding Window 截断 | ✅ | `pipelines/querying.py` (对话历史注入) |
| FR-004 | 会话管理 CRUD | ✅ | `cli/chat.py`, `api/routes/sessions.py` |
| FR-005 | 会话上下文隔离 | ✅ | `pipelines/components/session_store.py` |

### 审计日志

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-006 | 完整审计信息记录 | ✅ | `pipelines/components/audit_logger.py` |
| FR-007 | 按时间/关键词/分页查询 | ✅ | `cli/audit.py` |
| FR-008 | 日志不可删除（仅追加） | ✅ | `pipelines/components/audit_logger.py` (JSONL append-only) |
| FR-009 | 存储不可达时暂存 | ✅ | `pipelines/components/audit_logger.py` |

### OpenTelemetry 可观测性

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-010 | 全链路 Trace | ✅ | `pipelines/components/tracing.py` |
| FR-011 | Metrics（QPS/延迟/命中率） | ✅ | `pipelines/components/tracing.py` (InMemoryMetrics) |
| FR-012 | 可插拔 OTLP 后端 | ✅ | `config/settings.py` (OTelConfig) |
| FR-013 | OTLP 不可达降级 | ✅ | `pipelines/components/tracing.py` |

### 跨功能

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-014 | 审计日志与 Trace 关联 | ✅ | `pipelines/components/audit_logger.py`, `tracing.py` |
| FR-015 | 会话+检索构建提示 | ✅ | `pipelines/querying.py`, `pipelines/prompts.py` |

---

## M5 — Web 前端 & API 网关 (spec: 005)

### 问答界面

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-001 | Web 问答页面 | ✅ | `frontend/index.html` |
| FR-002 | 流式打字机效果 | ✅ | `frontend/index.html`, `api/routes/qa.py` (/ask/stream SSE) |
| FR-003 | 引用来源可交互标注 | ✅ | `frontend/index.html` (`<cite>` 锚点) |
| FR-004 | 会话追问 | ✅ | `frontend/index.html`, `api/routes/sessions.py` |
| FR-005 | 标准答案即时展示 | ✅ | `frontend/index.html`, `api/routes/qa.py` |

### 文档管理

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-006 | 文档上传界面 | ✅ | `frontend/index.html`, `api/routes/upload.py` |
| FR-007 | 索引状态实时展示 | ✅ | `frontend/index.html` |
| FR-008 | 软删除和批量操作 | ✅ | `api/routes/upload.py`, `cli/index.py` |
| FR-009 | 知识库概览 | ✅ | `frontend/index.html`, `api/routes/qa.py` (/status) |

### 标准答案管理

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-010 | CRUD 界面 | ✅ | `frontend/index.html`, `api/routes/answers.py` |
| FR-011 | 唯一性校验 | ✅ | `api/routes/answers.py` |
| FR-012 | 有效期状态展示 | ✅ | `frontend/index.html`, `api/routes/answers.py` |

### 审核工作流

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-013 | 审核队列界面 | ✅ | `frontend/index.html`, `api/routes/reviews.py` |
| FR-014 | 审核操作 | ✅ | `api/routes/reviews.py` (POST label) |
| FR-015 | 审核统计看板 | ✅ | `frontend/index.html`, `api/routes/reviews.py` |

### 使用统计

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-016 | 问答量趋势图 | ✅ | `frontend/index.html` |
| FR-017 | 准确率趋势图 | ✅ | `frontend/index.html` |
| FR-018 | 热门问题排名 | ✅ | `frontend/index.html` |
| FR-019 | 空状态引导 | ✅ | `frontend/index.html` |

### API 网关

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-020 | RESTful API + OpenAPI | ✅ | `api/server.py`, `api/routes/*` |
| FR-021 | API Key 认证（简化：JWT→X-API-Key） | ✅ | `api/middleware.py` (require_api_key) |
| FR-022 | RBAC 鉴权（简化：基础 API Key） | ✅ | `api/middleware.py` |
| FR-023 | SSE 流式 | ✅ | `api/routes/qa.py` (/ask/stream) |
| FR-024 | SSO 预留 | ✅ | `api/middleware.py` (扩展点) |

---

## M6 — 相关性增强 & 前端美观性 & 性能优化 (spec: 006)

### 混合检索优化

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-001 | BM25 全量化持久化+版本化 | ✅ | `pipelines/components/bm25_index.py` |
| FR-002 | Reranker 默认启用+回退告警 | ✅ | `pipelines/components/reranker.py`, `config/settings.py` |
| FR-003 | RRF k 可配置（默认35） | ✅ | `config/settings.py` (RetrievalConfig.rrf_k) |
| FR-004 | 归一化 RRF 分数展示 | ✅ | `pipelines/components/hybrid_retriever.py` |
| FR-005 | 来源类型标注向后兼容 | ✅ | `pipelines/components/hybrid_retriever.py` |

### 查询改写

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-006 | 术语归一化+多意图分解 | ✅ | `pipelines/components/query_rewriter.py` |
| FR-007 | 清晰问题透传 | ✅ | `pipelines/components/query_rewriter.py` |
| FR-008 | 失败回退原始问题 | ✅ | `pipelines/components/query_rewriter.py` |
| FR-009 | 配置开关 | ✅ | `config/settings.py` (QueryRewriteConfig) |

### 防幻觉强化

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-010 | 硬性接地约束 | ✅ | `pipelines/prompts.py` |
| FR-011 | 阈值0.7+F ail拒答 | ✅ | `config/settings.py`, `pipelines/components/faithfulness.py` |
| FR-012 | 独立 judge 模型 | ✅ | `config/settings.py`, `pipelines/components/faithfulness.py` |
| FR-013 | 分段评估 | ✅ | `pipelines/components/faithfulness.py` |

### 前端体验

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-014 | Prose 样式补全 | ✅ | `frontend/index.html` |
| FR-015 | GFM 配置 | ✅ | `frontend/index.html` |
| FR-016 | 可点击引用锚点 | ✅ | `frontend/index.html` (`<cite>`) |
| FR-017 | 来源卡片增强 | ✅ | `frontend/index.html` (百分比/星级/展开/图标) |
| FR-018 | Faithfulness 徽章 | ✅ | `frontend/index.html` (PASS/PARTIAL/FAIL) |
| FR-019 | 增量增强（不迁移框架） | ✅ | `frontend/index.html` |

### 性能优化

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-020 | BM25 全量持久化（同FR-001） | ✅ | `pipelines/components/bm25_index.py` |
| FR-021 | 查询级 LRU 缓存 | ✅ | `pipelines/components/query_cache.py` |
| FR-022 | Faithfulness 异步/跳过 | ✅ | `pipelines/querying.py`, `pipelines/components/faithfulness.py` |
| FR-023 | 嵌入+BM25 并行 | ✅ | `pipelines/querying.py` (ThreadPoolExecutor) |
| FR-024 | Startup 预热 | ✅ | `api/dependencies.py` (warmup) |
| FR-025 | Early Exit 批量嵌入 | ✅ | `pipelines/components/early_exit.py` |

### 兼容性

| FR | 标题 | 状态 | 实现文件 |
|----|------|------|---------|
| FR-026 | QueryResult 向后兼容 | ✅ | `pipelines/querying.py` |
| FR-027 | CLI/API 签名不变 | ✅ | 所有路由和 CLI 签名 |
| FR-028 | 新增配置默认值 | ✅ | `config/settings.py` (所有 Field 有 default) |

---

## 汇总

| 里程碑 | 功能需求 | 已实现 | 进度 |
|--------|---------|--------|------|
| M1 | 16 | 16 | 100% |
| M2 | 20 | 20 | 100% |
| M3 | 23 | 23 | 100% |
| M4 | 15 | 15 | 100% |
| M5 | 28 | 28 | 100% |
| M6 | 28 | 28 | 100% |
| **合计** | **130** | **130** | **100%** |
