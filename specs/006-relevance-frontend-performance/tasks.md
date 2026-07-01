# Tasks: 回答相关性、前端体验与性能增强 (M6)

> 对应 [plan.md](./plan.md) 的 5 个 Phase。任务按依赖顺序编号，`[ ]` 待办 / `[x]` 完成。
> 每个任务标注：**文件**、**验收**(对应 spec SC)、**依赖**。

---

## Phase 1 — 相关性 + 性能基石（BM25 全量化、Reranker、RRF 调优）

- [x] **T1.1** `turbovec_store.py` 暴露 chunk store 版本号
  - 文件: `src/qa/stores/turbovec_store.py`
  - 动作: StoreManager 增加 `store_version` 属性（基于 chunk 数量 + 索引文件 mtime 哈希），供 BM25 索引失效判定
  - 验收: 单元测试验证版本号在增量索引后变化、未变更时稳定
  - 依赖: 无

- [x] **T1.2** 新增 `bm25_index.py` 全量 BM25 索引模块
  - 文件: `src/qa/pipelines/components/bm25_index.py`（新）
  - 动作: 基于 `store_manager` 全量 chunk 构建 BM25Okapi；持久化到 `data/bm25/{version}.pkl`；按 `store_version` 失效重建；提供 `load_or_build(store_manager)` 与 `retrieve(query_text, top_k)` 接口
  - 验收: SC-008 加载 p95 < 50ms；`test_bm25_index.py` 覆盖构建/持久化/版本失效/加载；**增量索引后失效**——新增/删除文档使 `store_version` 变化时，BM25 索引自动重建
  - 依赖: T1.1

- [x] **T1.3** 改造 `hybrid_retriever.py` 使用全量 BM25 + RRF 调优 + 分数归一化
  - 文件: `src/qa/pipelines/components/hybrid_retriever.py`
  - 动作: BM25 检索从"每请求重建"改为调用 `bm25_index.load_or_build()`；`rrf_k` 改为构造参数（默认 35）；展示分数改归一化 RRF 分数（`score / max_score`）
  - 验收: SC-001 召回率 ≥ 1.15x（50 条术语测试集对比）；`test_hybrid_retriever_v2.py` 验证全量召回与归一化分数
  - 依赖: T1.2

- [x] **T1.4** `settings.py` RRF k 可配 + Reranker 默认开
  - 文件: `src/qa/config/settings.py`
  - 动作: `RetrievalConfig` 新增 `rrf_k: int = 35`；`RerankConfig.enabled` 默认改 `true`
  - 验收: 默认配置下 Reranker 激活；`rrf_k` 可通过环境变量 `qa_retrieval_rrf_k` 覆盖
  - 依赖: 无（可与 T1.3 并行）

- [x] **T1.5** Reranker 不可用回退告警
  - 文件: `src/qa/pipelines/components/reranker.py`、`src/qa/pipelines/querying.py`
  - 动作: Reranker 调用异常时记录 WARNING 日志并回退到 RRF 排序结果（当前 `querying.py` 已有 `except: pass`，改为 `logger.warning` + 保留原排序）
  - 验收: 模拟 rerank API 不可用，问答不中断、日志含回退告警
  - 依赖: T1.4

---

## Phase 2 — 查询改写 + 防幻觉强化

- [x] **T2.1** 新增 `QueryRewriteConfig` 配置
  - 文件: `src/qa/config/settings.py`
  - 动作: 新增 `QueryRewriteConfig`（`enabled: bool = True`、`model: str = ""`（空则复用 LLM）、`timeout_seconds: float = 3.0`、`term_map_path: str = "./data/term_map.json"`）；挂到 `Settings`
  - 验收: `as_dict()` 含新配置；环境变量 `qa_query_rewrite_enabled` 可覆盖
  - 依赖: 无

- [x] **T2.2** 新增 `query_rewriter.py` 查询改写层
  - 文件: `src/qa/pipelines/components/query_rewriter.py`（新）
  - 动作: (1) 术语归一化——加载 `term_map.json`（证券简称→全称映射），正则替换；(2) 多意图分解——LLM 调用拆复合问题为子问题列表；(3) 清晰单一问题透传不调 LLM（启发式规则：问题中不含多意图连词（和/与/及/以及/分别/同时）且问号数 ≤1 则透传；含连词时仍调 LLM 做轻量判定，超时 3s 回退原问题）；(4) 失败/超时回退原始问题
  - 验收: `test_query_rewriter.py` 覆盖归一化/分解/透传/回退，并定义明确边界用例集：单一术语问题（透传）、含"和"的复合问题（分解）、纯术语问题含"和"但为单一术语（透传，如"清算和交收系统"若为专有术语则不拆）；SC-003 改写后准确率 +10%
  - 依赖: T2.1

- [x] **T2.3** `querying.py` 接入查询改写层
  - 文件: `src/qa/pipelines/querying.py`
  - 动作: 在 `_embed_query` 之前调用 `query_rewriter.rewrite(question)`；多子问题分别嵌入检索后合并候选（去重 + 重新 RRF）；`QueryResult` 新增可选字段 `rewritten_question: str = ""`
  - 验收: 非流式与流式路径均接入；复合问题回答覆盖多意图
  - 依赖: T2.2、T1.3

- [x] **T2.4** system_prompt 硬接地约束
  - 文件: `src/qa/pipelines/querying.py`
  - 动作: `_generate` 与 `_generate_stream` 的 system_prompt 增加："若无任何片段支撑该问题，必须仅回复'根据现有知识库无法回答该问题'，不得拼凑"
  - 验收: 知识库无支撑片段时 LLM 仅回复拒答语；不拼凑
  - 依赖: 无

- [x] **T2.5** Faithfulness 阈值/judge_model/分段
  - 文件: `src/qa/config/settings.py`、`src/qa/pipelines/components/faithfulness.py`
  - 动作: `FaithfulnessConfig.threshold` 默认 0.7；新增 `judge_model: str = ""`（空则复用主 LLM）；`max_claims` 改为分段策略（按段落聚合声明，不再硬截断 10）；FAIL 时降级为拒答不附加原文
  - 验收: SC-004 整体准确率 ≥ 88%；半数无支撑判 FAIL 并拒答；judge 独立模型可配
  - 依赖: 无

---

## Phase 3 — 性能优化（缓存、并行、预热、批量嵌入）

- [x] **T3.1** 新增 `QueryCacheConfig` + `query_cache.py`
  - 文件: `src/qa/config/settings.py`、`src/qa/pipelines/components/query_cache.py`（新）
  - 动作: LRU 缓存，key = `(归一化问题, top_k, filters_hash)`；`max_size: int = 256`、`ttl_seconds: float = 300`；索引版本变更时整体失效（监听 `store_version`）
  - 验收: `test_query_cache.py` 覆盖命中/淘汰/TTL 失效/版本失效；SC-009 命中 < 100ms
  - 依赖: T1.1

- [x] **T3.2** `querying.py` 接入查询缓存 + embed/BM25 并行
  - 文件: `src/qa/pipelines/querying.py`
  - 动作: `run()` 在 Early Exit 之后检查缓存，命中直接返回；未命中执行全链路后写入缓存；embed 与 BM25 检索用 `concurrent.futures.ThreadPoolExecutor` 并行（二者无数据依赖）
  - 验收: 重复问题第 2 次命中缓存；并行后检索延迟下降
  - 依赖: T3.1、T1.3

- [x] **T3.3** `server.py` startup 预热
  - 文件: `src/qa/api/server.py`
  - 动作: `@app.on_event("startup")` 中预加载 store、`bm25_index.load_or_build()`、预热 embedder（触发一次空嵌入）；预热失败记录 ERROR 但不阻塞启动
  - 验收: SC-011 冷启动首请求 < 2s；预热失败不阻断服务
  - 依赖: T1.2

- [x] **T3.4** 非流式 Faithfulness 异步化
  - 文件: `src/qa/pipelines/querying.py`、`src/qa/api/server.py`
  - 动作: 非流式 `run()` 中 faithfulness 改异步执行（`asyncio.create_task` 或线程池），回答先返回；低风险场景（`from_standard_answer` 或检索最高分 ≥ 跳过阈值）跳过校验；结果通过 `QueryResult.faithfulness` 后续填充
  - 验收: SC-010 非流式 p95 < 4s；低风险跳过不产生校验延迟
  - 注意: 跳过阈值待 T1.3 分数归一化落地后校准——当前 0.8 基于向量余弦，归一化 RRF 分数范围不同，须重新确定阈值并在 `config.yaml` 暴露为可配项
  - 依赖: T2.5

- [x] **T3.5** Early Exit `match_batch` 批量嵌入
  - 文件: `src/qa/pipelines/components/early_exit.py`
  - 动作: `match_batch` 改为单次批量嵌入所有标准答案（当前是循环调 `match()`），减少 embedding API 往返
  - 验收: 批量嵌入调用次数 = 1（不论标准答案数量）
  - 依赖: 无

---

## Phase 4 — 前端体验增强

- [x] **T4.1** prose 样式补全 + marked GFM 配置
  - 文件: `frontend/index.html`
  - 动作: `.message-body` 下补全 `table/th/td`（边框+表头底色+斑马纹）、`h1-h3`、`blockquote`、`strong`、`hr`；封装 `renderMarkdown(text)` 统一调用 `marked.parse(text, { breaks: true, gfm: true })`，替换所有 `marked.parse` 调用点
  - 验收: SC-005 含表格/标题/列表回答渲染正确率 100%（20 条样本）
  - 依赖: 无

- [x] **T4.2** 引用 `[来源:文件名]` 可点击 `<cite>`
  - 文件: `frontend/index.html`
  - 动作: `finalizeMessage`/`renderMessage` 渲染后扫描回答中引用标记，**优先匹配** `[来源:xxx]` 格式；若无 `来源:` 前缀，则匹配 `[xxx]` 格式但要求文件名长度 ≥ 3 字符避免误匹配短词；按文件名匹配 sources 列表索引，替换为 `<cite data-source-idx="N" onclick="focusSource(N)">`；点击展开对应来源卡片并高亮；无法匹配的保留纯文本
  - 验收: SC-006 引用跳转覆盖 ≥ 95%；`[来源:xxx]` 优先于 `[xxx]`；短文件名（< 3 字符）的 `[xxx]` 格式不误匹配
  - 依赖: T4.1

- [x] **T4.3** 来源卡片升级（分数可视化 + 展开全文 + 图标）
  - 文件: `frontend/index.html`
  - 动作: 分数改百分比（`(score*100).toFixed(0)%`）或星级（score ≥0.8 三星/≥0.5 二星/else 一星）；内容片段显示前 200 字 + "展开全文"按钮；按文件扩展名加图标（📄 PDF / 📝 Word / 📋 MD / 🌐 HTML）
  - 验收: 来源卡片无裸浮点；展开全文功能可用
  - 依赖: T4.1

- [x] **T4.4** Faithfulness 可信度徽章
  - 文件: `frontend/index.html`
  - 动作: `finalizeMessage` 读取 faithfulness（流式从 `onFaithfulness` 回调、非流式从 `result.faithfulness`）渲染徽章：PASS 绿 / PARTIAL 黄 / FAIL 红，附 score；`renderMessage` 同步处理非流式路径
  - 验收: SC-007 徽章覆盖 100%（含流式与非流式）
  - 依赖: T4.1

---

## Phase 5 — 配置同步、文档、回归

- [x] **T5.1** `config.example.yaml` 新增配置块
  - 文件: `config.example.yaml`
  - 动作: 新增 `query_rewrite`、`bm25_persist`、`query_cache` 块及注释；更新 `rerank.enabled: true`、`faithfulness.threshold: 0.7`、`retrieval.rrf_k: 35`
  - 验收: SC-012 默认配置无改动 config.yaml 可运行
  - 依赖: T2.1、T3.1、T1.4

- [x] **T5.2** 内置术语映射种子数据
  - 文件: `data/term_map.json`（新，gitignored 但提供 `data/term_map.example.json`）
  - 动作: 录入证券清算常见简称→全称映射（如 中登→中国证券登记结算有限责任公司、CSDC→中国证券登记结算有限责任公司、上交所→上海证券交易所 等 ≥ 20 条）
  - 验收: 查询改写单元测试使用该映射通过
  - 依赖: T2.2

- [x] **T5.3** 文档更新
  - 文件: `README.md`、`CLAUDE.md`
  - 动作: README 更新性能指标（p95 < 4s / 缓存 < 100ms）、新增配置说明；CLAUDE.md 追加 M6 变更记录
  - 验收: 文档与实现一致
  - 依赖: 所有前序任务

- [x] **T5.4** 全量回归
  - 命令: `ruff check --fix && ruff format && mypy && pytest`
  - 动作: 新增测试纳入既有流水线；验证无回归
  - 验收: CI 全绿；测试覆盖率维持 ≥ 80%
  - 依赖: 所有前序任务

---

## 任务依赖图

```
T1.1 ──→ T1.2 ──→ T1.3 ──→ T2.3 ──→ T3.2
                  │         │
T1.4 ──→ T1.5    │         T2.1 ──→ T2.2
                  │
T2.1 ──→ T2.2    T2.4 (独立)
T2.5 (独立) ──→ T3.4
T3.1 ──→ T3.2
T3.3 (依赖 T1.2)
T3.5 (独立)
T4.x (独立, 可与 Phase 1-3 并行)
T5.x (收尾, 依赖前序)
```

**并行机会**: Phase 4（前端）与 Phase 1-3 完全独立，可并行开发；T2.4/T2.5/T3.5 相互独立可并行。
