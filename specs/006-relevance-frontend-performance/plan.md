# Implementation Plan: 回答相关性、前端体验与性能增强 (M6)

**Branch**: `006-relevance-frontend-performance` | **Date**: 2026-06-10 | **Spec**: [spec.md](./spec.md)

## Summary

对已闭合的 M1-M5 能力栈做三项增量增强：(1) 混合检索全量化 + Reranker 默认开 + 查询改写 + 防幻觉强化，提升回答相关性；(2) 前端单页 HTML 补全 prose 样式、引用可点击、来源卡片升级、可信度徽章，提升展示美观性；(3) BM25 持久化 + 查询缓存 + 检索并行化 + Faithfulness 异步 + 冷启动预热，提升响应性能。不重构架构、不引入框架、完全向后兼容。

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: 复用现有栈——haystack-ai、rank_bm25、openai、pydantic-settings、fastapi；前端 marked.min.js。M6 **不引入新依赖**。

**Storage**: turbovec（向量索引，已有）+ 新增 BM25 持久化索引（`data/bm25/`，按 chunk store 版本号命名失效）+ 查询缓存（内存 LRU）

**Testing**: pytest + pytest-cov（维持 80% 覆盖率）；前端无自动化测试基线，M6 不新增前端测试框架

**Target Platform**: 同 M1（Linux 服务器 / Docker），前端为静态单页 HTML

**Performance Goals**: 非流式 p95 < 4s（M2 基线 6s）、缓存命中 p95 < 100ms、BM25 加载 p95 < 50ms、冷启动首请求 < 2s

**Constraints**: 完全向后兼容（QueryResult/API/CLI 签名不变）、配置项有默认值无需改 config.yaml、所有增强可回退不阻塞主链路

**Scale/Scope**: 增量优化型，改动集中在检索/生成/前端三层，不触碰 RBAC/多轮对话/审核工作流

## Constitution Check

*GATE: Must pass before implementation. Re-check after design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Code Quality | ✅ PASS | 增量改造既有组件，遵循 SOLID/DRY，新增配置遵循 `qa_*` 前缀惯例 |
| II. Test-First | ✅ PASS | 每个新增组件配套单元测试；混合检索召回率对比测试（SC-001）；前端改动人工验证 + 样本回归 |
| III. UX Consistency | ✅ PASS | 前端基于现有金融主题 CSS 变量增量增强，不破坏既有视觉语言 |
| IV. Performance | ✅ PASS | 量化目标 SC-008~012 已定义，BM25 持久化与缓存有明确延迟阈值 |
| V. Security | ✅ PASS | 不改变认证鉴权（M5 RBAC）；查询缓存 key 不含敏感数据；BM25 持久化文件在 data/（已 gitignore） |
| VI. Simplicity | ✅ PASS | YAGNI——前端不迁移框架、BM25 不替换为 PG；每项增强可独立回退 |
| VII. Documentation | ⚠️ NOTE | 需更新 config.example.yaml 注释、README 性能指标、CLAUDE.md 变更记录 |
| VIII. CI/CD | ✅ PASS | ruff + mypy + pytest 门禁不变，新增测试纳入既有流水线 |

## Project Structure

### 改动文件（增量修改）

```text
src/qa/
├── config/
│   └── settings.py                    # 新增 QueryRewriteConfig、BM25PersistConfig、QueryCacheConfig；调整 RetrievalConfig.rrf_k、RerankConfig.enabled 默认、FaithfulnessConfig 阈值/judge_model/max_claims
├── pipelines/
│   ├── querying.py                    # 接入查询改写、system_prompt 硬接地、Faithfulness 异步/跳过、embed+BM25 并行
│   └── components/
│       ├── hybrid_retriever.py        # BM25 改为加载全量持久化索引；RRF k 可配；展示分数归一化
│       ├── reranker.py                # 无代码改动（仅默认启用），补充回退告警日志
│       ├── faithfulness.py            # judge_model 支持、阈值默认 0.7、max_claims 改分段
│       └── early_exit.py              # match_batch 批量嵌入
├── api/
│   └── server.py                      # startup 预热事件、查询缓存中间件、非流式 faithfulness 异步
└── stores/
    └── turbovec_store.py              # 暴露 chunk store 版本号（供 BM25 索引失效判定）

frontend/
└── index.html                         # prose 样式补全、marked GFM 配置、引用<cite>后处理、来源卡片升级、可信度徽章

config.example.yaml                    # 新增 query_rewrite / bm25_persist / query_cache 配置块及注释
```

### 新增文件

```text
src/qa/pipelines/components/
├── query_rewriter.py                  # 查询改写层：术语归一化 + 多意图分解
├── bm25_index.py                      # 全量 BM25 索引构建/持久化/版本化加载
└── query_cache.py                     # 查询级 LRU 缓存（key: 归一化问题+top_k+filters）

tests/unit/
├── test_query_rewriter.py             # 改写/回退/透传
├── test_bm25_index.py                 # 构建/持久化/版本失效/加载
├── test_query_cache.py                # 命中/淘汰/Early Exit 优先/版本失效
└── test_hybrid_retriever_v2.py        # 全量 BM25 召回对比、RRF 分数归一化

tests/integration/
└── test_m6_pipeline.py                # 端到端：改写→混合检索→Reranker→生成→异步 faithfulness
```

## Implementation Phases

### Phase 1 — 相关性 + 性能基石（BM25 全量化、Reranker、RRF 调优）

**改动**: `bm25_index.py`(新)、`hybrid_retriever.py`、`settings.py`(RetrievalConfig.rrf_k)、`turbovec_store.py`(版本号)

- 新增 `bm25_index.py`：基于 `store_manager` 全量 chunk 构建 BM25Okapi，持久化到 `data/bm25/{version}.pkl`，按 chunk store 版本号失效重建。
- `hybrid_retriever.py`：BM25 检索从"每请求重建"改为"加载持久化索引独立召回"；`rrf_k` 改为可配（默认 35）；展示分数改归一化 RRF。
- `turbovec_store.py`：暴露 `store_version` 属性供失效判定。

**验证**: `test_bm25_index.py`、`test_hybrid_retriever_v2.py`；SC-001 召回率 ≥ 1.15x；SC-008 加载 p95 < 50ms。

> **发布约束**: FR-004（展示分数改归一化 RRF）改变 `sources[].score` 数值语义，须与 Phase 4 的 T4.3（分数可视化）同批发布，避免中间态分数突变。若 Phase 1 先独立发布，须在 T1.3 中暂时保留向量余弦作为展示分数，待 T4.3 落地后再切换。

### Phase 2 — 查询改写 + 防幻觉强化

**改动**: `query_rewriter.py`(新)、`querying.py`、`faithfulness.py`、`settings.py`(QueryRewriteConfig、FaithfulnessConfig)

- `query_rewriter.py`：术语归一化（内置证券简称→全称映射表）+ 多意图分解（LLM 调用，失败回退）。`enabled` 默认开，可关。
- `querying.py`：嵌入前接入改写层；system_prompt 增加硬接地约束；非流式 `run()` 中 faithfulness 异步或低风险跳过。
- `faithfulness.py`：`judge_model` 可配；阈值默认 0.7；`max_claims` 改分段聚合。

**验证**: `test_query_rewriter.py`；SC-003 改写后准确率 +10%；SC-004 整体准确率 ≥ 88%。

### Phase 3 — 性能优化（缓存、并行、预热、批量嵌入）

**改动**: `query_cache.py`(新)、`server.py`、`querying.py`、`early_exit.py`、`settings.py`(QueryCacheConfig)

- `query_cache.py`：LRU，key = `(归一化问题, top_k, filters_hash)`，索引版本变更整体失效。检查点在 Early Exit 之后。
- `server.py`：`@app.on_event("startup")` 预热 store/BM25/embedder；非流式 ask 接入缓存。
- `querying.py`：embed 与 BM25 检索并行（线程池）。
- `early_exit.py`：`match_batch` 单次批量嵌入。

**验证**: `test_query_cache.py`、`test_m6_pipeline.py`；SC-009 缓存命中 < 100ms；SC-010 非流式 p95 < 4s；SC-011 冷启动 < 2s。

### Phase 4 — 前端体验增强

**改动**: `frontend/index.html`（单文件）

- prose 样式：`.message-body` 下补全 `table/th/td`（边框+表头底色+斑马纹）、`h1-h3`、`blockquote`、`strong`、`hr`。
- marked 配置：`marked.parse(text, { breaks: true, gfm: true })`（全局封装一处）。
- 引用后处理：`finalizeMessage`/`renderMessage` 后扫描 `[来源:xxx]`，替换为 `<cite data-source-idx>`，点击展开对应来源卡片并高亮。
- 来源卡片：分数改百分比/星级；内容片段"展开全文"；文件类型图标。
- 可信度徽章：`finalizeMessage` 读取 faithfulness 渲染 PASS/PARTIAL/FAIL 徽章；流式复用 `onFaithfulness`。

**验证**: 20 条含表格/标题/列表回答样本渲染正确率 100%（SC-005）；引用跳转覆盖 ≥ 95%（SC-006）；徽章覆盖 100%（SC-007）。

### Phase 5 — 配置同步、文档、回归

**改动**: `config.example.yaml`、`README.md`、`CLAUDE.md`

- `config.example.yaml`：新增 `query_rewrite`、`bm25_persist`、`query_cache` 块及注释；更新 `rerank.enabled: true`、`faithfulness.threshold: 0.7`。
- `README.md`：更新性能指标、新增配置说明。
- `CLAUDE.md`：M6 变更记录。
- 全量回归：`ruff check --fix && ruff format && mypy && pytest`。

**验证**: SC-012 默认配置无改动可运行；CI 全绿。

## Complexity Tracking

> 无 Constitution 违规需额外说明。主要复杂度风险点：
> - **BM25 全量索引构建耗时**：万级文档首次构建可能数秒，通过持久化 + 异步构建 + 降级回退缓解（Edge Case: 构建期间回退向量检索）。
> - **查询改写引入额外 LLM 调用**：增加 ~200ms 延迟，通过 `enabled` 开关与失败回退控制；标准问题透传不调用。
> - **Faithfulness 异步化的一致性**：非流式返回时校验可能未完成，前端通过轮询或后续事件补全徽章（Edge Case 已覆盖）。
