# Feature Specification: Securities Clearing & Technology Knowledge QA Agent (M1)

**Feature Branch**: `001-securities-qa-agent`

**Created**: 2026-06-09

**Status**: Draft

**Input**: User description: "在 haystack 的基础上做一个证券行业的清算和技术相关的知识问答类的 llm agent。参考 wiki/turbovec/docs/integrations/haystack.md，使用 turbovec 替换默认向量数据库。文档包含 PDF/Word/Markdown/HTML 多种格式，清算条文含大量表格、交叉引用。"

## Background

证券清算业务规则复杂、技术文档繁多，业务人员查找信息效率低且易出错。核心问题：

1. **信息分散**：清算规则、技术手册散落多个系统，难以快速定位
2. **准确率低**：人工解答依赖个人经验，缺乏标准化
3. **知识流失**：专家经验未沉淀，人员流动导致知识断层

M1 聚焦最小可用问答闭环：文档导入 → 向量检索 → LLM 生成 → 引用溯源。

> 后续里程碑：M2 混合检索+防幻觉、M3 标准答案+人工审核、M4 多轮对话+可观测性、M5 Web 前端+API 网关。每个里程碑独立创建 spec。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Securities Knowledge Question Answering (Priority: P1)

证券行业从业人员输入关于证券清算规则、技术架构、业务流程的自然语言问题，系统从 turbovec 向量知识库中检索相关文档片段，结合 LLM 生成准确、附引用来源的专业回答。

**Why this priority**: 核心价值——没有问答能力系统无用。验证 turbovec 替换方案可行性的必要条件。

**Independent Test**: 输入"沪深交易所 T+1 清算流程是什么？"测试完整问答链路（检索+生成+引用）。

**Acceptance Scenarios**:

1. **Given** 知识库已索引证券清算文档, **When** 用户输入"银行间债券市场清算方式有哪些？", **Then** 系统返回包含引用来源的专业回答，引用片段与问题相关且准确。
2. **Given** 知识库已索引, **When** 用户输入超出知识库范围的问题（如"今天天气怎么样？"）, **Then** 系统诚实告知无法回答，不编造信息。
3. **Given** 知识库包含多个相关文档, **When** 用户输入模糊问题（如"CSDC 的作用"）, **Then** 系统综合多个来源给出全面回答，列出各引用来源。

---

### User Story 2 - Multi-format Document Ingestion (Priority: P1)

业务人员通过 CLI 命令将 PDF、Word、Markdown、HTML 等格式的证券文档导入知识库。系统自动识别文档类型、提取文本和表格内容、分块、计算嵌入向量并存入 turbovec 索引。

**Why this priority**: 没有文档就无法问答。这是 P1 的前置依赖，与 US1 同为 MVP 必需。

**Independent Test**: 传入一个含表格的 PDF 文件和一份 Markdown 文件，验证两者均被正确解析、分块并可在检索中命中。

**Acceptance Scenarios**:

1. **Given** 一份含清算规则表格的 PDF 文件, **When** 执行索引命令, **Then** PDF 内容（含表格文本）被正确提取、分块、嵌入并写入 turbovec 索引。
2. **Given** 一份 Markdown 格式的技术手册, **When** 执行索引命令, **Then** Markdown 内容被正确解析（保留标题层级）、分块、嵌入并写入 turbovec 索引。
3. **Given** 多种格式文件混合目录, **When** 执行批量索引命令, **Then** FileTypeRouter 正确分流各格式文件，每种格式由对应解析器处理，全部内容成功入库。

---

### User Story 3 - TurboVec Vector Retrieval (Priority: P2)

系统使用 TurboQuantDocumentStore（turbovec）替代 Haystack 默认 InMemoryDocumentStore 作为向量后端，实现 8x 内存压缩和 SIMD 加速检索，同时保持与 Haystack Pipeline 的完全兼容。

**Why this priority**: 这是 M1 的技术差异化核心，但 US1 可先验证端到端链路后再替换。纯技术验证，P2 优先级合理。

**Independent Test**: 对比相同查询在 InMemoryDocumentStore vs TurboQuantDocumentStore 下检索结果的一致性和性能。

**Acceptance Scenarios**:

1. **Given** 同一批文档分别写入 InMemoryDocumentStore 和 TurboQuantDocumentStore, **When** 执行相同 embedding_retrieval 查询, **Then** 两者 top-5 结果中至少 4 条相同。
2. **Given** 10 万条文档的 turbovec 索引, **When** 执行检索查询, **Then** turbovec 内存占用低于等量 InMemoryDocumentStore 的 20%。
3. **Given** turbovec 索引已 save_to_disk, **When** 系统 load_from_disk 后执行查询, **Then** 检索结果与持久化前一致。

---

### Edge Cases

- 知识库为空时，用户提问应返回"知识库尚未建立，请先导入文档"的明确提示。
- 用户输入极长问题（超过 LLM token 限制），系统应截断后检索，不崩溃。
- PDF 中的扫描图片（无 OCR 文本）应跳过并记录警告日志，不阻塞整个索引流程。
- 含特殊字符的文档内容（清算代码、证书编号等）应正确处理，不丢失信息。
- 文档表格内容提取后，关键限定条件（如阈值、日期）不应错漏。
- LLM 或 Embedding API 不可用时，系统应返回明确错误提示"服务暂时不可用，请稍后重试"，不做缓存重试或降级到本地模型。
- API 调用超时时（超过可配置阈值），系统应返回超时错误而非无限等待。
- 文档缺少必需元数据字段（source/category/effective_date）时，系统应拒绝导入并明确列出缺失字段，不静默跳过。

## Requirements *(mandatory)*

### Functional Requirements

**问答核心**

- **FR-001**: 系统 MUST 接受用户自然语言问题，返回基于知识库内容的专业回答。
- **FR-002**: 系统 MUST 使用 Haystack 2.x Pipeline 架构组织问答流程（索引 → 嵌入 → 检索 → LLM 生成）。
- **FR-003**: 系统 MUST 在回答中包含引用来源，标注检索到的文档片段出处（文件名、段落位置）。
- **FR-004**: 系统 MUST 对超出知识库范围的问题返回"无法回答"提示，不编造信息（防幻觉基线）。
- **FR-004a**: 系统 MUST 在 LLM 或 Embedding API 不可用时返回明确错误提示"服务暂时不可用，请稍后重试"，不做降级重试或缓存回退。
- **FR-004b**: 系统 MUST 在 API 调用超过可配置超时阈值时返回超时错误，而非无限等待。

**向量检索**

- **FR-005**: 系统 MUST 使用 TurboQuantDocumentStore（turbovec）替代 InMemoryDocumentStore 作为向量存储后端，默认 bit_width=4（平衡精度与内存），支持通过配置切换 bit_width=2（极限压缩场景）。
- **FR-006**: 系统 MUST 支持基于元数据的过滤检索（按来源、类别、版本筛选），利用 turbovec allowlist 过滤。
- **FR-007**: 系统 MUST 支持索引磁盘持久化（save_to_disk / load_from_disk）和增量更新（DuplicatePolicy.SKIP/OVERWRITE）。

**文档导入**

- **FR-008**: 系统 MUST 支持 PDF、Word (.docx)、Markdown、HTML 四种格式的文档导入，通过 FileTypeRouter 自动分流。
- **FR-009**: 系统 MUST 对 PDF 中的表格内容进行提取，保留关键数据不丢失（限定条件、阈值、日期等）。
- **FR-010**: 系统 MUST 对 Markdown 文档保留标题层级结构，确保分块时跨标题的内容不被割裂。
- **FR-011**: 系统 MUST 采用分层文档检索策略（Hierarchical Document Retrieval）：小块（句子/段落级）用于精准检索，命中后返回其所属父级大块（章节级）给 LLM，确保条文限定条件不错漏。
- **FR-012**: 系统 MUST 支持文档元数据标注，分为必需字段和可选字段：(a) 必需字段：source（来源机构）、category（文档类别）、effective_date（生效日期），缺少必需字段的文档 MUST 被拒绝导入并给出明确提示；(b) 可选字段：version（版本号）、tags（标签列表）、description（摘要），缺失时不阻塞导入。元数据 MUST 可在检索时用于过滤。

**接口**

- **FR-013**: 系统 MUST 提供 CLI 命令用于知识库管理（索引构建、文档增删、查询测试），支持两种模式：(a) 单次命令模式（如 `qa ask "问题"`、`qa index ./docs`），适合脚本化调用；(b) 交互式会话模式（如 `qa chat`），适合探索性多轮使用。
- **FR-014**: 系统 MUST 提供 CLI 命令查询知识库状态（文档数量、索引大小、最近更新时间），如 `qa status`。

### Key Entities

- **KnowledgeDocument**: 证券清算与技术知识的原始文档。属性：内容、元数据（必需：source/category/effective_date；可选：version/tags/description）、预计算嵌入向量、文档格式。
- **DocumentChunk**: 按分层策略切分的文档片段。小块（段落级）用于检索，大块（章节级）用于 LLM 上下文。属性：原始内容、父级块内容、所属文档 ID、在原文中的位置、层级深度（section/paragraph）。
- **VectorIndex**: 基于 TurboQuant 的向量索引。属性：量化维度、比特宽度（默认 4-bit，可配置 2-bit）、相似度函数、磁盘持久化路径。
- **QueryResult**: 一次问答的完整输出。属性：用户问题、检索到的片段列表、LLM 生成的回答、引用来源列表、查询耗时。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 证券清算领域常见问题回答准确率 ≥ 80%（基于人工评估 50 个基准问题，M1 阶段基线）。
- **SC-002**: 单次问答端到端响应时间 p95 < 5 秒（含检索 + LLM 生成）。
- **SC-003**: 向量检索阶段响应时间 p95 < 500ms（10 万文档规模）。
- **SC-004**: turbovec 索引内存占用 ≤ 同等数据量 InMemoryDocumentStore 的 20%。
- **SC-005**: 90% 的引用来源与回答内容直接相关，无幻觉引用。
- **SC-006**: PDF 表格内容提取完整性 ≥ 95%（关键数值不错漏）。
- **SC-007**: 知识库全量索引构建可成功完成 1000 篇文档（含 PDF 和 Markdown）。
- **SC-008**: 索引磁盘持久化和加载均 < 60 秒。

## Clarifications

### Session 2026-06-09

- Q: 文档分块策略？ → A: 分层文档检索 — 小块精准检索，返回其所属父级大块给 LLM，天然匹配条文章节结构
- Q: CLI 交互模式？ → A: 单次命令 + 可选交互模式 — 默认 `qa ask "问题"` 单次问答；`qa chat` 进入交互式会话
- Q: LLM/API 失败降级策略？ → A: 直接报错 — API 不可用时返回明确错误提示"服务暂时不可用，请稍后重试"，不使用缓存或备选模型
- Q: turbovec bit_width 配置？ → A: 默认 4-bit，可配置切换 2-bit — 生产环境 4-bit 平衡精度和内存；高级用户可切换 2-bit 追求极限压缩
- Q: 文档元数据 schema？ → A: 必需字段 + 可选扩展 — 必需：source(来源机构)、category(文档类别)、effective_date(生效日期)；可选：version(版本号)、tags(标签列表)、description(摘要)。缺少必需字段时拒绝导入

## Assumptions

- 目标用户为证券行业从业人员（交易员、清算专员、合规人员），具备基础领域知识。
- 知识库初始规模 1000-10000 篇文档，后续可能扩展到 10 万级。
- LLM 后端使用内部 OpenAI 兼容 API（数据不出境），需网络连接。
- 文档嵌入向量需预计算（turbovec 不负责嵌入生成，通过 SentenceTransformers 或内部 API 计算）。
- 嵌入模型和 LLM 模型选择在实现阶段确定，spec 不限定具体模型。
- 部署环境为 Python 3.11+，服务器可用 Docker 或 systemd。
- M1 阶段仅支持单轮问答，多轮对话在 M4 实现。
- M1 阶段仅 CLI 交互，Web 前端在 M5 实现。
- M1 阶段不做混合检索（PG 全文检索）和 Reranker，在 M2 实现。
- M1 阶段不做 Early Exit（标准答案库），在 M2 实现。
- M1 阶段不做人工审核工作流，在 M3 实现。
- M1 阶段不做 OpenTelemetry 可观测性，在 M4 实现。