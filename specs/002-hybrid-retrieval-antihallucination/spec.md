# Feature Specification: 混合检索 + 防幻觉 (M2)

**Feature Branch**: `002-hybrid-retrieval-antihallucination`
**Created**: 2026-06-09
**Status**: Draft
**Input**: M2 混合检索 + 防幻觉 + Early Exit

## Background

M1 已完成核心 RAG 闭环：文档导入 → turbovec 向量检索 → LLM 单轮问答 → 引用溯源。M1 的检索依赖纯向量相似度，对专有术语（清算代码、缩写）召回率不足；且无幻觉校验机制，LLM 可能生成无检索支撑的内容。

M2 在 M1 基础上扩展三项能力：

1. **混合检索**：引入全文检索（倒排索引）与向量检索互补，再经 Reranker 精排，提升召回率和排序质量。
2. **Early Exit**：对标准答案库精确匹配命中的问题，跳过 LLM 生成，直接返回标准答案——零幻觉、零延迟。
3. **Faithfulness 校验**：LLM 生成后，自动检验回答是否有检索片段支撑；不通过则降级提示，形成防幻觉闭环。

> 后续里程碑：M3 人工审核工作流、M4 多轮对话 + 可观测性、M5 Web 前端 + API 网关。每个里程碑独立创建 spec。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 混合检索提升专业术语召回 (Priority: P1)

证券清算人员输入含专有缩写或精准术语的问题（如"CCASS 交收指令截止时间"），系统同时从向量索引和全文索引检索候选片段，经精排后返回更准确、更完整的回答，引用来源覆盖单纯向量检索遗漏的文档。

**Why this priority**: 混合检索是 M2 核心价值——解决 M1 向量检索对术语/缩写召回不足的痛点，直接影响回答质量。

**Independent Test**: 对含专有缩写的查询，对比纯向量检索与混合检索的 top-10 召回率，验证混合检索召回率提升 ≥ 15%。

**Acceptance Scenarios**:

1. **Given** 知识库已索引含"CCASS"缩写的文档, **When** 用户输入"CCASS 交收流程", **Then** 混合检索 top-10 结果包含该文档，而纯向量检索 top-10 结果不含。
2. **Given** 知识库已索引中英文混合术语的文档, **When** 用户输入包含精准术语的问题, **Then** 混合检索的 top-5 精排结果至少有 3 条与问题高相关。
3. **Given** 知识库包含语义相似但术语不同的两个文档集合, **When** 用户问一个术语精准的问题, **Then** 全文检索命中术语精确匹配的文档，向量检索命中语义相关文档，精排后两者均出现在 top-5。

---

### User Story 2 - 标准答案 Early Exit (Priority: P1)

高频标准问题（如"沪深交易所 A 股清算周期是多少？"）已在标准答案库中录入，系统判断命中后直接返回标准答案，不经 LLM 生成，响应极快且 100% 准确。

**Why this priority**: Early Exit 是防幻觉最有效的手段——跳过 LLM 即消除幻觉风险，同时极大降低延迟和成本。与混合检索同为 P1，两者互补。

**Independent Test**: 输入一个标准答案库中已存在的问题，验证系统直接返回标准答案（不走 LLM），响应时间 < 100ms，回答与库中答案一致。

**Acceptance Scenarios**:

1. **Given** 标准答案库含"沪深交易所 A 股清算周期"，**When** 用户输入"沪深交易所 A 股清算周期是多少？", **Then** 系统直接返回标准答案，不调用 LLM，响应时间 < 100ms。
2. **Given** 标准答案库含标准问答对, **When** 用户输入与标准问题语义相似但措辞不同的问题, **Then** 系统通过模糊匹配命中标准答案并返回，标记来源为"标准答案库"。
3. **Given** 标准答案库未覆盖用户问题, **When** 用户输入问题, **Then** 系统正常走混合检索 + LLM 生成流程，不返回空结果或错误。

---

### User Story 3 - 忠实度校验防幻觉 (Priority: P2)

LLM 生成回答后，系统自动校验回答是否有检索片段支撑。无支撑的声明被标记；严重偏离时系统降级为"无法确认"提示，保护用户不被误导。

**Why this priority**: 防幻觉是 M2 的重要能力，但 Early Exit 已覆盖标准场景，Faithfulness 校验作为兜底机制，P2 合理。

**Independent Test**: 构造一个 LLM 容易编造回答的问题（知识库无直接答案），验证 Faithfulness 校验不通过时系统降级提示。

**Acceptance Scenarios**:

1. **Given** 知识库含部分相关文档但无直接答案, **When** LLM 生成包含编造细节的回答, **Then** Faithfulness Evaluator 判定不通过，系统标注哪些声明无检索支撑。
2. **Given** 知识库无任何相关文档, **When** LLM 生成与检索片段严重偏离的回答, **Then** 系统降级返回"根据当前知识库无法确认该问题的答案"的提示。
3. **Given** 知识库有充分相关文档, **When** LLM 生成忠实于检索片段的回答, **Then** Faithfulness Evaluator 判定通过，回答正常输出。

---

### User Story 4 - 标准答案管理与种子数据 (Priority: P2)

管理员通过 CLI 将标准问答对录入标准答案库，M2 提供种子数据（证券清算高频 50 问），支持增删改查和批量导入。

**Why this priority**: 标准答案库是 Early Exit 的数据基础，必需但数据量小、模型简单，P2 实现。

**Independent Test**: 通过 CLI 录入一条标准问答对，查询验证命中并返回正确答案。

**Acceptance Scenarios**:

1. **Given** 管理员通过 CLI 提交标准问答对（JSON 格式）, **When** 执行录入命令, **Then** 问答对持久化存储，后续查询可精确命中。
2. **Given** 种子数据文件（50 条高频问答）, **When** 执行批量导入命令, **Then** 所有问答对成功入库，可通过查询逐一命中验证。
3. **Given** 标准答案库已有问答对, **When** 管理员删除或更新一条, **Then** 后续查询反映最新状态（删除后不再命中，更新后返回新答案）。

---

### Edge Cases

- 标准答案库与 LLM 生成答案出现矛盾时，标准答案优先，系统不返回 LLM 版本。
- 混合检索中向量检索和全文检索均无结果时，系统直接返回"未找到相关信息"，不调用 LLM 生成。
- Reranker 评分全部低于阈值时，系统返回低置信度提示，而非强行拼凑回答。
- PDF 表格内的清算代码在全文索引中需保持原格式（不丢失特殊字符），否则术语精确匹配失效。
- Faithfulness Evaluator 本身误判（将忠实回答判为不忠实）时，系统应在回答中附加说明"本回答经自动化校验，可能存在标注偏差"，而非直接隐藏回答。

## Requirements *(mandatory)*

### Functional Requirements

**混合检索**

- **FR-001**: 系统 MUST 同时执行向量检索和全文检索，合并两者候选结果后送入 Reranker 精排，最终输出排序后的检索片段。
- **FR-002**: 系统 MUST 使用 PostgreSQL（pg_trgm + tsvector）替代 InMemoryBM25Retriever 作为全文检索后端，支持中文分词和模糊匹配。
- **FR-003**: 系统 MUST 在混合检索 Pipeline 中使用 Reranker 组件对合并候选集精排，提升最终排序质量。
- **FR-004**: 系统 MUST 支持配置向量检索和全文检索的候选数量（top_k）及 Reranker 精排数量（top_n），参数可通过配置文件调整。
- **FR-005**: 系统 MUST 对混合检索结果标注来源类型（向量命中 / 全文命中 / 双重命中），供下游组件和用户追溯。

**Early Exit**

- **FR-006**: 系统 MUST 在问答流程前端设置 ConditionalRouter，优先匹配标准答案库；命中时直接返回标准答案，跳过 LLM 生成和 Faithfulness 校验。
- **FR-007**: 系统 MUST 支持标准答案的精确匹配和模糊匹配（语义相似度阈值可配）两种命中策略。
- **FR-008**: 系统 MUST 对 Early Exit 命中的回答标注来源为"标准答案库"，与 LLM 生成回答明确区分。
- **FR-009**: 系统 MUST 在标准答案库未命中时，无缝降级到混合检索 + LLM 生成流程。

**标准答案库**

- **FR-010**: 系统 MUST 提供标准答案数据模型，包含：问题文本、标准答案文本、类别标签、生效日期、录入来源。
- **FR-011**: 系统 MUST 提供 CLI 命令支持标准问答对的增删改查和批量导入（JSON 格式）。
- **FR-012**: 系统 MUST 内置 M2 种子数据集（≥ 50 条证券清算高频标准问答对），系统初始化时可加载。
- **FR-013**: 系统 MUST 对标准答案库中的模糊匹配命中记录日志（含匹配分数），支持审计和调优。

**防幻觉（Faithfulness）**

- **FR-014**: 系统 MUST 在 LLM 生成回答后，使用 Faithfulness Evaluator 检验回答是否被检索片段支撑。
- **FR-015**: 系统 MUST 对 Faithfulness 校验不通过（严重偏离）的回答，降级返回"无法确认"提示或附加无支撑声明标注。
- **FR-016**: 系统 MUST 对 Faithfulness 校验通过的回答，在输出中附加校验通过标识。
- **FR-017**: 系统 MUST 记录每次 Faithfulness 校验的结果（通过/不通过/部分通过）及详细评分，供后续优化和审计。

**接口与兼容**

- **FR-018**: 系统 MUST 保持与 M1 问答 CLI 命令的向后兼容（相同输入输出格式），混合检索和防幻觉逻辑在 Pipeline 内部透明升级。
- **FR-019**: 系统 MUST 提供混合检索独立测试 CLI 命令，支持传入查询并对比向量检索、全文检索和混合检索的结果差异。
- **FR-020**: 系统 MUST 提供标准答案库管理 CLI 命令（导入、查询、删除、批量操作）。

### Key Entities

- **StandardAnswer**: 标准答案库中的问答对。属性：问题文本、标准答案文本、类别标签、生效日期、录入来源、匹配策略（精确/模糊）。
- **SearchCandidate**: 混合检索的候选片段。属性：文档内容、元数据、来源类型（向量/全文/双重）、向量相似度分数、全文相关度分数。
- **RerankedResult**: Reranker 精排后的最终检索结果。属性：排序后的候选列表、精排分数、来源类型标注。
- **FaithfulnessReport**: 忠实度校验报告。属性：回答文本、校验结果（通过/不通过/部分通过）、无支撑声明列表、详细评分、降级处理方式。
- **QueryResult**（M1 扩展）: 新增属性——检索来源类型标注、FaithfulnessReport、是否 Early Exit 命中。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 混合检索对含专有术语查询的召回率 ≥ 纯向量检索的 1.15 倍（基于 50 条术语测试集）。
- **SC-002**: Reranker 精排后 top-5 结果与人工标注的相关性评分 ≥ 4.0（5 分制，基于 30 条测试查询）。
- **SC-003**: 标准答案 Early Exit 命中时，响应时间 p95 < 100ms（不含 LLM 调用）。
- **SC-004**: 标准答案库精确匹配准确率 100%，模糊匹配准确率 ≥ 90%（基于 50 条种子数据 + 10 条近义变体测试）。
- **SC-005**: Faithfulness Evaluator 对忠实回答的通过率 ≥ 95%，对编造回答的拦截率 ≥ 80%（基于 30 条标注测试集）。
- **SC-006**: 非标准答案查询端到端响应时间 p95 < 6 秒（含混合检索 + Reranker + LLM 生成 + Faithfulness 校验）。
- **SC-007**: 向量检索 + 全文检索阶段响应时间 p95 < 500ms（10 万文档规模）。
- **SC-008**: 证券清算领域常见问题整体回答准确率 ≥ 85%（含 Early Exit + LLM 生成，基于 80 条基准问题，M1 基线为 80%）。

## Assumptions

- M1 核心问答 Pipeline 已完成并可正常运行，M2 在此基础上扩展。
- PostgreSQL 实例可部署或复用现有实例，需启用 pg_trgm 和中文分词扩展。
- 全文检索使用 PostgreSQL tsvector + pg_trgm 模糊匹配，中文分词依赖 jieba 或 pg_jieba 扩展。
- Reranker 使用内部 OpenAI 兼容 API 或 BGE-Reranker 本地部署，spec 不限定具体模型。
- Faithfulness Evaluator 使用 Haystack 内置组件或 LLM-as-judge 方案，实现阶段确定。
- 标准答案库 M2 阶段通过 CLI 管理，无 Web 界面（M5 实现）。
- 标准答案库种子数据由领域专家提供，M2 内置 ≥ 50 条。
- Early Exit 的模糊匹配使用向量相似度，阈值可在配置文件中调整。
- M2 不含人工审核工作流（M3 实现），标准答案库内容由直接录入完成。
- M2 不含多轮对话（M4 实现），所有问答仍为单轮。
- M2 不含 OpenTelemetry 可观测性（M4 实现）。
- M2 不含 Web 前端和 API 网关（M5 实现）。