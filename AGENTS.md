<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
specs/001-securities-qa-agent/plan.md

Key artifacts:
- Spec: specs/001-securities-qa-agent/spec.md
- Research: specs/001-securities-qa-agent/research.md
- Data Model: specs/001-securities-qa-agent/data-model.md
- Quickstart: specs/001-securities-qa-agent/quickstart.md
- Contracts: specs/001-securities-qa-agent/contracts/
<!-- SPECKIT END -->

# M3/M4/M5 Polish 变更记录 (2026-06-22)

## 新增/变更概要

### M3 — 审核反馈闭环验证
- **集成测试**: `tests/integration/test_review_feedback_loop.py` 验证 review→standard answer→early_exit 端到端闭环
- **API 契约测试**: `tests/contract/test_review_api.py` POST `/qa/reviews/{id}/label` 响应+副作用验证

### M4 — API 网关强化
- **API 密钥认证**: `ServerConfig.api_keys` 配置 + `X-API-Key` 头鉴权（`Depends()` 粒度），绕过健康检查/状态查询只读端点
- **限流器**: 内存滑动窗口（`ServerConfig.rate_limit_rpm: int = 60`），响应头 `X-RateLimit-Limit`/`X-RateLimit-Remaining`，429 超限拒绝
- **请求日志**: 每条 API 请求记录 method/path/latency/status/API key(masked)，复用 `audit_logger`
- **新增文件**: `src/qa/api/middleware.py`（~140 行，包含 `check_rate_limit`/`log_request_middleware`）
- **配置新增**: `server.api_keys`, `server.rate_limit_rpm`, `server.allowed_origins`

### M5 — 部署加固
- **CORS 硬化**: 替换 `allow_origins=["*"]` → `settings.server.allowed_origins`，默认 `["http://localhost:8001"]`
- **Dockerfile**: 多阶段构建（builder uv pip install → runtime python-slim），暴露 8001，健康检查，非 root 用户
- **docker-compose.yml**: qa-agent 服务，build 本地镜像 + volume 挂载 `data/`
- **CI**: `.github/workflows/ci.yml` — push master/PR 触发，ruff → mypy → pytest
- **部署文档**: `docs/deployment.md`

### 策略
- 所有新增配置字段有安全默认值（空 = 不鉴权、0 = 不限流），向后兼容零改动

---

# M6 变更记录 (2026-06-10)

## 新增/变更概要

### 相关性增强
- **BM25 全量化**: 新增 `bm25_index.py`，基于 `store_manager` 全量 chunk 构建 BM25Okapi 并持久化到 `data/bm25/{version}.pkl`，按 chunk store 版本号失效重建
- **RRF k 调优**: `rrf_k` 默认从 60 → 35（`RetrievalConfig.rrf_k`），提升排序区分度
- **Reranker 默认开**: `RerankConfig.enabled` 默认 `true`
- **分数归一化**: 展示分数从向量余弦改为归一化 RRF 分数（FR-004）
- **查询改写**: 新增 `query_rewriter.py`（术语归一化 + 多意图分解），`QueryRewriteConfig`
- **防幻觉强化**: `FaithfulnessConfig.threshold` 默认 0.7；新增 `judge_model`（独立评判模型）

### 前端美观性（frontend/index.html）
- Prose 样式：table/th/td(边框+斑马纹)、h1-h3、blockquote、hr
- marked GFM 配置：`{ breaks: true, gfm: true }`
- 引用 `[来源:文件名]` 可点击 `<cite>`，展开高亮对应来源
- 来源卡片：分数百分比/星级、展开全文、文件类型图标
- Faithfulness 可信度徽章（PASS ✅ / PARTIAL ⚠️ / FAIL ❌）

### 性能优化
- **查询缓存**: 新增 `query_cache.py`（LRU + TTL + 索引版本失效），`QueryCacheConfig`
- **Startup 预热**: `server.py` @app.on_event("startup") 预加载 store/BM25/embedder
- **Faithfulness 低风险跳过**: 检索最高分 ≥ 0.95 时跳过（`_is_low_risk_context`）
- **批量嵌入**: `early_exit.py match_batch` 改为单次 `embed_texts` 批量嵌入

### 种子数据
- `data/term_map.example.json`：25 条证券清算领域简称→全称映射表

### 配置（config.example.yaml）
- 新增 `retrieval.rrf_k: 35`
- `rerank.enabled: true`
- `faithfulness.threshold: 0.7` + `judge_model`
- 新增 `query_rewrite` / `query_cache` 配置块

## 性能目标
| 指标 | 目标 | 对比基线 |
|------|------|----------|
| 非流式 p95 | < 4s | M2 基线 6s |
| 缓存命中 p95 | < 100ms | 无缓存 |
| BM25 加载 p95 | < 50ms | 每请求 O(n) 重建 |
| 冷启动首请求 | < 2s | 懒加载尖峰 |

## 向后兼容性
- `QueryResult` / API 响应结构不变（新增 optional 字段）
- 所有新增配置项有默认值，无需改动已有 config.yaml
- `sources[].score` 语义从向量余弦→归一化 RRF（与前端同批发布）
