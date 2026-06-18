# Feature Specification: 回答相关性、前端体验与性能增强 (M6)

**Feature Branch**: `006-relevance-frontend-performance`
**Created**: 2026-06-10
**Status**: Draft
**Input**: M6 回答相关性提升 + 前端内容美观性 + 性能优化

## Background

M1-M5 已建立完整能力栈：核心 RAG 闭环（M1）、混合检索+防幻觉（M2）、标准答案与审核（M3）、多轮对话与可观测性（M4）、Web 前端与 API 网关（M5）。功能闭环已闭合，但**回答质量、展示体验与响应性能**三个维度仍存在可量化短板，直接影响终端用户的使用价值。

经代码审计，定位到三类具体缺陷：

1. **相关性瓶颈**——混合检索名不副实：BM25 索引每次 query 在向量检索结果上**重建**（定义 `hybrid_retriever.py:52-60`，调用点 `hybrid_retriever.py:137`），纯关键词相关但向量距离远的文档永远进不了候选池；Reranker 默认关闭（`settings.py:173`）；RRF 融合参数 `k=60` 过大导致排序区分度低（`hybrid_retriever.py:22`）；无查询改写，原始口语化问题直接嵌入。
2. **前端展示粗糙**——Markdown 表格/标题在 `index.html` 中无对应样式规则，证券文档大量表格渲染成裸 HTML 错位难读；引用 `[来源:文件名]` 是纯文本不可点击；来源卡片分数显示裸浮点（如 `0.005`）；Faithfulness 可信度在流式外未渲染徽章。
3. **性能浪费**——BM25 每请求重建是 O(n) 重复计算；查询嵌入无缓存；非流式 `run()` 中 Faithfulness 同步阻塞主链路增加 1-3s；冷启动 store 懒加载导致首请求延迟尖峰。

M6 针对上述三点做**增量增强**，不重构既有架构、不引入新框架，以最小改动获取最大质量收益。

> 本里程碑为优化型增量，非新建能力。M1-M5 所有契约保持向后兼容。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 混合检索召回率提升 (Priority: P1)

证券清算人员输入含专有缩写或精准术语的问题（如"CCASS 交收指令截止时间"），系统从**全量语料**的向量索引和全文索引并行召回候选，经 Reranker 精排后返回更准确、更完整的回答，覆盖单纯向量检索遗漏的文档。

**Why this priority**: BM25 全量化是相关性与性能的双重单点收益——当前 BM25 退化为向量结果的重排序器，混合检索的核心价值未兑现。这是 M6 最高优先级。

**Independent Test**: 对含专有缩写的查询，对比改造前后混合检索的 top-10 召回率，验证召回率提升 ≥ 15%。

**Acceptance Scenarios**:

1. **Given** 知识库已索引含"CCASS"缩写但与查询向量距离远的文档, **When** 用户输入"CCASS 交收流程", **Then** 全量 BM25 检索独立召回该文档，混合 top-10 结果包含它，而改造前纯向量检索 top-10 不含。
2. **Given** Reranker 已启用, **When** 混合检索返回 top_k×2 候选, **Then** Reranker 精排后 top-5 至少 3 条与问题高相关，且排序区分度较 RRF 原始排序提升。
3. **Given** 用户查询同时命中向量与全文两个模态, **When** 系统返回结果, **Then** 来源类型标注为"双重命中"且展示分数为归一化 RRF 分数，而非误导性的向量余弦值。

---

### User Story 2 - 查询理解与改写 (Priority: P1)

用户输入口语化、含缩写或多意图的问题（如"CSDC 登记和结算分别怎么操作"），系统在嵌入前对问题做术语归一化与多意图分解，分别检索后合并，提升复杂问题的召回质量。

**Why this priority**: 证券领域问题常含简称、口语化表述或多段意图，原始问题直接嵌入导致召回偏移。查询改写是相关性深化的关键一环。

**Independent Test**: 输入一个含两段意图的复合问题，验证系统拆分为子问题分别检索后合并，回答覆盖两段意图。

**Acceptance Scenarios**:

1. **Given** 用户输入"中登公司的作用", **When** 查询改写层将"中登公司"归一化为"中国证券登记结算有限责任公司(CSDC)", **Then** 嵌入与检索使用归一化后的术语，召回质量较原始问题提升。
2. **Given** 用户输入"登记和结算分别怎么操作"复合问题, **When** 查询改写层分解为"登记操作流程"和"结算操作流程"两个子问题, **Then** 系统分别检索并合并候选，回答同时覆盖登记与结算两段。
3. **Given** 用户输入清晰单一的标准问题, **When** 查询改写层判定无需改写, **Then** 原始问题直接进入嵌入，不引入额外延迟或改写噪声。

---

### User Story 3 - 防幻觉约束强化 (Priority: P2)

LLM 生成回答后，系统以更严格的标准校验接地性：接地约束强化、Faithfulness 阈值提升、校验模型独立化，严重无支撑时直接拒答而非附加原文。

**Why this priority**: 防幻觉是兜底机制，Early Exit 已覆盖标准场景，Faithfulness 强化作为质量护栏，P2 合理。

**Independent Test**: 构造一个知识库无直接答案的问题，验证强化后的接地约束使 LLM 明确拒答，而非拼凑部分信息。

**Acceptance Scenarios**:

1. **Given** 知识库无任何支撑片段, **When** LLM 生成回答, **Then** system_prompt 的硬性接地约束使 LLM 仅回复"根据现有知识库无法回答该问题"，不拼凑。
2. **Given** LLM 生成半数声明无支撑的回答, **When** Faithfulness 校验（阈值 0.7）, **Then** 判定 FAIL 并降级为拒答提示，不附加原文。
3. **Given** Faithfulness judge 使用独立模型, **When** 校验生成回答, **Then** 不存在生成模型自评的确认偏差，校验结果更客观。

---

### User Story 4 - 前端回答排版与引用交互 (Priority: P1)

业务人员在 Web 界面获取回答时，Markdown 表格/标题/列表正确渲染，引用 `[来源:文件名]` 可点击跳转对应来源卡片，来源卡片支持展开全文与相关性可视化，回答附带可信度徽章。

**Why this priority**: 证券文档大量含表格，当前裸 HTML 渲染严重影响可读性；引用不可点击导致溯源体验断裂。这是用户感知最直接的体验短板。

**Independent Test**: 提问一个返回含表格内容的问题，验证表格正确渲染、引用可点击展开来源、可信度徽章显示。

**Acceptance Scenarios**:

1. **Given** LLM 回答含 Markdown 表格, **When** 前端渲染回答, **Then** 表格带边框、表头底色、斑马纹，列对齐正确，无裸 HTML 错位。
2. **Given** 回答文本含 `[来源:清算规则.pdf]` 引用, **When** 用户点击该引用, **Then** 对应来源卡片自动展开并高亮，实现回答与来源的溯源联动。
3. **Given** Faithfulness 校验结果为 PARTIAL, **When** 回答渲染完成, **Then** 回答底部显示黄色"部分可信"徽章，PASS 显示绿色、FAIL 显示红色。
4. **Given** 来源卡片展示相关性分数, **When** 渲染来源列表, **Then** 分数以百分比或星级呈现，不显示裸浮点。

---

### User Story 5 - 端到端性能优化 (Priority: P2)

系统通过 BM25 索引持久化、查询级缓存、检索并行化、Faithfulness 异步化与冷启动预热，降低重复计算与阻塞延迟，p95 响应时间显著下降。

**Why this priority**: 性能是体验的乘数，但功能已闭合，优化作为收尾。BM25 持久化与 US1 同一改动，其余为独立优化项。

**Independent Test**: 连续发送 10 次相同问题，验证第 2 次起命中查询缓存响应时间 < 100ms；冷启动后首次问答延迟 < 2s。

**Acceptance Scenarios**:

1. **Given** BM25 全量索引已持久化, **When** 用户提问, **Then** 检索阶段不再重建索引，BM25 加载耗时 < 50ms，较每请求重建显著下降。
2. **Given** 查询缓存已启用, **When** 用户重复发送相同问题, **Then** 第 2 次起命中缓存直接返回，端到端 < 100ms。
3. **Given** 非流式问答场景, **When** Faithfulness 校验执行, **Then** 校验异步进行不阻塞回答返回，或在低风险场景（standard answer / 高分检索）跳过，主链路延迟不增加。
4. **Given** 服务刚启动, **When** 首个用户提问, **Then** 因 startup 预热已加载 store 与 BM25 索引，首请求延迟无尖峰。

---

### Edge Cases

- 全量 BM25 索引构建期间用户提问，系统应回退到向量检索并记录降级日志，不阻塞问答。
- 索引增量更新（新增/删除文档）后，BM25 持久化索引须按 store 版本号失效重建，不返回陈旧结果。
- 查询改写层对超长问题（超过 LLM token 限制）应截断后再改写，不崩溃。
- 查询改写失败（LLM 不可用）时，系统回退到原始问题直接嵌入，不中断问答。
- 查询缓存与 Early Exit 标准答案库出现冲突时，标准答案优先（Early Exit 在缓存检查之前）。
- 前端引用解析遇到无法匹配来源列表的文件名时，保留为纯文本不报错，不影响回答渲染。
- Faithfulness 异步化后校验结果晚于回答返回时，前端通过后续事件更新徽章，不丢失可信度信息。
- Reranker 服务不可用时，系统回退到 RRF 排序并记录告警，不中断问答。

## Requirements *(mandatory)*

### Functional Requirements

**混合检索优化**

- **FR-001**: 系统 MUST 将 BM25 索引改为基于**全量语料**构建并持久化到磁盘，按 chunk store 版本号失效重建，消除每请求 O(n) 重建。query 时直接加载持久化索引，加载耗时 < 50ms。
- **FR-002**: 系统 MUST 默认启用 Reranker（`rerank.enabled` 默认 `true`），对混合检索 top_k×2 候选精排，输出 top_k 结果。Reranker 服务不可用时回退到 RRF 排序并告警。
- **FR-003**: 系统 MUST 将 RRF 融合参数 `rrf_k` 从 60 调整为可配置（默认 30~40），提升排序区分度。
- **FR-004**: 系统 MUST 将展示分数从向量余弦改为**归一化 RRF 分数**，消除纯 BM25 命中文档的误导性分数。
- **FR-005**: 系统 MUST 保持来源类型标注（vector/bm25/hybrid）语义不变，向后兼容 M2 FR-005。

**查询改写**

- **FR-006**: 系统 MUST 在嵌入前新增查询改写层，支持术语归一化（证券简称→全称映射）与多意图分解（复合问题拆子问题）。
- **FR-007**: 查询改写层 MUST 对清晰单一的标准问题判定"无需改写"并直接透传，避免引入噪声与延迟。
- **FR-008**: 查询改写失败（LLM 不可用/超时）时，系统 MUST 回退到原始问题直接嵌入，不中断问答。
- **FR-009**: 查询改写 MUST 可通过配置开关启停（`query_rewrite.enabled`），默认启用但允许关闭以回归基线。

**防幻觉强化**

- **FR-010**: system_prompt MUST 增加硬性接地约束："若无任何片段支撑该问题，必须仅回复'根据现有知识库无法回答该问题'，不得拼凑"。
- **FR-011**: Faithfulness 通过阈值 MUST 从 0.5 提升至 0.7（可配置）；FAIL 时直接降级为拒答提示，不附加原文。
- **FR-012**: Faithfulness judge MUST 支持配置独立模型（`faithfulness.judge_model`），避免生成模型自评的确认偏差；未配置时回退到主 LLM。
- **FR-013**: 系统 MUST 取消 `max_claims` 硬截断，改为分段评估或按段落聚合声明，避免长答案的不支撑声明被静默忽略。

**前端体验**

- **FR-014**: 前端 MUST 为 `.message-body` 补全完整 prose 样式：`table/th/td`（边框、表头底色、斑马纹）、`h1-h3`、`blockquote`、`strong`、`hr`、`ul/ol`。
- **FR-015**: 前端 MUST 配置 marked 启用 GFM 表格与换行（`{ breaks: true, gfm: true }`），确保中文换行与表格正确渲染。
- **FR-016**: 前端 MUST 在答案渲染后后处理 `[来源:文件名]` / `[文件名]` 文本，替换为可点击 `<cite>` 锚点，点击展开并高亮对应来源卡片。
- **FR-017**: 前端来源卡片 MUST 支持内容片段"展开全文"、文件类型图标、相关性以百分比或星级呈现（不显示裸浮点）。
- **FR-018**: 前端 MUST 在回答渲染完成时显示 Faithfulness 可信度徽章（PASS 绿/PARTIAL 黄/FAIL 红），复用流式 `onFaithfulness` 回调数据；非流式场景从 `QueryResult.faithfulness` 读取。
- **FR-019**: 前端改动 MUST 基于**现有单页 HTML**（`frontend/index.html` + `marked.min.js`）增量增强，不引入框架迁移。

**性能优化**

- **FR-020**: 系统 MUST 实现 BM25 索引全量持久化与版本化失效（与 FR-001 同一实现），消除每请求重建开销。
- **FR-021**: 系统 MUST 实现查询级 LRU 缓存，key 为 `(question 归一化, top_k, filters)`，命中直接返回。缓存检查在 Early Exit 之后。
- **FR-022**: 非流式 `run()` 中 Faithfulness 校验 MUST 异步执行或对低风险场景（standard answer / 检索最高分 ≥ 阈值）跳过，不阻塞回答返回。
- **FR-023**: 系统 MUST 并行化查询嵌入与 BM25 检索（二者无数据依赖），使用线程池或 asyncio 并发执行。
- **FR-024**: API 服务 MUST 在 startup 事件中预热：预加载 store、预建/加载 BM25 索引、预热 embedder，消除首请求延迟尖峰。
- **FR-025**: Early Exit 的 `match_batch` MUST 改为单次批量嵌入所有标准答案，减少 embedding API 往返。

**兼容性**

- **FR-026**: 系统 MUST 保持 `QueryResult` 与 `/api/v1/qa/ask` 响应结构向后兼容；新增字段（归一化分数、改写后问题等）为可选，不破坏现有前端。
- **FR-027**: 系统 MUST 保持 M1-M5 所有 CLI 命令与 API 端点签名不变，增强逻辑在 Pipeline 内部透明升级。
- **FR-028**: 所有新增配置项 MUST 提供合理默认值，系统在未显式配置时按默认值运行，不要求用户改动现有 `config.yaml`。

### Key Entities

- **GlobalBM25Index**: 全量语料的 BM25 倒排索引。属性：词项字典、文档频率、版本号（对应 chunk store 版本）、持久化路径。按版本号失效重建。
- **QueryTransformResult**: 查询改写结果。属性：原始问题、改写后问题（或子问题列表）、是否改写标志、改写耗时。
- **QueryCacheEntry**: 查询缓存条目。属性：归一化问题、top_k、filters 哈希、QueryResult、写入时间戳。LRU 淘汰。
- **CitationAnchor**: 回答内引用锚点。属性：文件名、匹配的来源索引、渲染后的 `<cite>` 元素。前端运行时实体。
- **FaithfulnessConfig（扩展）**: 新增 `judge_model`、阈值默认 0.7、`max_claims` 改为分段策略。扩展 M2 实体。
- **BM25Index（扩展）**: 从每次重建升级为全量持久化。扩展 M2 实体。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 混合检索对含专有术语查询的召回率 ≥ 改造前的 1.15 倍（基于 50 条术语测试集，对比 top-10）。
- **SC-002**: Reranker 精排后 top-5 结果与人工标注的相关性评分 ≥ 4.0（5 分制，基于 30 条测试查询）。
- **SC-003**: 查询改写后，复合/口语化问题的回答准确率较基线提升 ≥ 10%（基于 30 条口语化测试集，基线为 M6 改造前同一测试集的准确率）。
- **SC-004**: 证券清算领域常见问题整体回答准确率 ≥ 88%（M2 基线 85%，基于 80 条基准问题）。
- **SC-005**: 前端 Markdown 表格、标题、列表渲染正确率 100%（基于 20 条含结构化内容回答样本）。
- **SC-006**: 回答内引用可点击跳转来源卡片覆盖率 ≥ 95%（无法匹配文件名的引用保留纯文本）。
- **SC-007**: Faithfulness 可信度徽章在所有回答中渲染覆盖率 100%（含流式与非流式）。
- **SC-008**: BM25 索引加载耗时 p95 < 50ms（全量持久化后，10 万文档规模）。
- **SC-009**: 查询缓存命中时端到端响应时间 p95 < 100ms。
- **SC-010**: 非流式问答端到端响应时间 p95 < 4 秒（M2 基线 6 秒，含 Faithfulness 异步化收益）。
- **SC-011**: 冷启动后首个问答请求延迟 < 2 秒（startup 预热后）。
- **SC-012**: 所有新增配置项在默认值下系统正常运行，无需用户改动 `config.yaml`。

## Clarifications

### Session 2026-06-10

- Q: BM25 全量化是否引入新依赖？ → A: 否——复用现有 `rank_bm25`，改为全量构建并持久化，不替换为 PostgreSQL 全文检索（M2 已有 PG 可选路径，M6 不强制切换）。
- Q: 查询改写是否引入新 LLM 调用？ → A: 是，轻量调用——但失败时回退原始问题，不中断；可通过配置关闭以回归基线。
- Q: 前端是否迁移到 React/Vue？ → A: 否——基于现有单页 HTML 增量增强，遵循 YAGNI；005 spec 假设的 React 栈与本仓库实际不符，M6 以实际代码为准。
- Q: Reranker 默认开启是否增加部署门槛？ → A: 复用现有 `reranker.py` 实现，仅需提供 rerank API 端点；不可用时自动回退 RRF 排序，不阻塞。
- Q: Faithfulness 异步化后可信度徽章如何更新？ → A: 流式场景通过后续 SSE 事件更新；非流式场景从 `QueryResult.faithfulness` 读取，若校验晚于返回则前端轮询或后续事件补全。
- Q: 查询缓存与数据一致性如何保证？ → A: 缓存 key 含 top_k/filters，索引版本变更时整体失效；Early Exit 优先于缓存检查，标准答案始终最新。
- Q: FR-003（RRF k 调整）和 FR-013（取消 max_claims 截断）为何无独立 SC？ → A: 两者是 SC-001（召回率）和 SC-004（整体准确率）的实现手段，属于内部调优参数，不设面向用户的独立度量指标。
- Q: FR-004 分数归一化与前端 T4.3 分数可视化的发布顺序？ → A: 两者需同批发布——FR-004 改变 `sources[].score` 数值语义（向量余弦→归一化 RRF），若后端先发布而前端未同步，分数显示会突变。plan.md 已标注此约束。

## Assumptions

- M1-M5 全部能力已就绪并可正常运行，M6 在此基础上做增量增强。
- 现有 `rank_bm25`、`reranker.py`、`faithfulness.py`、`early_exit.py` 实现可被改造，无需推翻重写。
- Reranker API 端点可部署或复用现有内部 OpenAI 兼容 `/rerank` 端点；不可用时回退不影响主流程。
- 查询改写使用主 LLM 或轻量模型，spec 不限定具体模型，实现阶段确定。
- 前端基线为 `frontend/index.html`（单页原生 HTML + `marked.min.js`），不引入框架、不迁移构建工具。
- 配置体系基于现有 Pydantic Settings（`config/settings.py`），新增项遵循 `qa_*` 环境变量前缀惯例。
- BM25 持久化索引存储于 `data/` 目录（已在 `.gitignore`），按 chunk store 版本号命名失效。
- 性能指标基于本地/内网部署环境（LAN 延迟 < 10ms），不含公网网络波动。
- M6 不改变认证鉴权体系（M5 RBAC）、不改变多轮对话能力（M4）、不改变审核工作流（M3）。
- M6 不引入新的外部依赖（如新向量库、新框架），所有改动在现有技术栈内完成。
