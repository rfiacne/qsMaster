# Feature Specification: 多轮对话与可观测性 (M4)

**Feature Branch**: `004-conversation-observability`
**Created**: 2026-06-09
**Status**: Draft
**Input**: M4 多轮对话 + 审计日志 + OpenTelemetry 可观测性

## Background

M1 完成了核心 RAG 管道（文档导入 → turbovec 向量检索 → 单轮问答 → 引用溯源），M2 增加了混合检索（PG 全文检索 + Reranker 精排）、Early Exit 和 Faithfulness Evaluator，M3 增加了标准答案管理和人工审核工作流。

经过 M1-M3，系统已具备：
- 完整的单轮问答链路（向量检索 + 全文检索 + 重排序 + LLM 生成 + 引用溯源）
- 防幻觉机制（Early Exit + Faithfulness 评估）
- 标准答案管理（CRUD + 人工审核工作流）

但仍存在三大短板：

1. **单轮限制**：每次问答独立，无法利用上下文进行追问，用户体验割裂。例如用户问"中登公司的清算流程是什么？"后想追问"那和上交所的有何区别？"必须重新描述背景。
2. **无审计留痕**：证券行业监管要求操作留痕，当前系统无任何问答记录，无法回溯谁在何时问了什么、系统返回了什么。
3. **黑箱运行**：各环节（检索、排序、生成、评估）缺乏可观测性，故障发生时无法定位是哪个环节出了问题，也无法量化系统运行质量。

M4 在 M3 基础上扩展：持久化会话记忆实现多轮对话、PostgreSQL 审计日志满足监管留痕要求、OpenTelemetry 全链路可观测性实现故障定位和质量度量。

> 后续里程碑：M5 Web 前端 + API 网关（JWT 认证 + RBAC）。M4 仅 CLI 交互，无 Web 界面。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 多轮追问式问答 (Priority: P0)

证券清算专员在研究某项业务规则时，需要连续追问以逐步深入理解。系统在会话上下文中保留前几轮对话，使追问自然衔接，无需重复描述背景。

**Why this priority**: 多轮对话是系统从"单次检索工具"升级为"交互式知识助手"的关键能力，直接影响用户使用效率和体验。

**Independent Test**: 创建一个会话，先问"沪深交易所 T+1 清算流程是什么？"，再追问"那和银行间债券市场的清算方式有什么区别？"，验证第二问能引用第一问的上下文。

**Acceptance Scenarios**:

1. **Given** 用户已创建会话, **When** 用户连续提问"中登公司结算流程？"→"和上交所的区别？"→"费用怎么算？", **Then** 后续问题能基于前文上下文生成连贯回答，不需要重复描述"中登公司"。
2. **Given** 会话已积累 8 轮对话, **When** 用户继续提问, **Then** 系统自动截断至最近 5 轮对话 / 4000 tokens 以内，保证回复质量不退化。
3. **Given** 服务重启后, **When** 用户恢复之前会话继续提问, **Then** 会话历史完整恢复，对话无缝衔接。

---

### User Story 2 - 审计日志追溯 (Priority: P0)

合规审计人员需要追溯特定时段内所有用户与系统的交互记录，包括问题内容、检索片段、排序结果、生成回答、完整性评分和耗时，满足证券行业操作留痕监管要求。

**Why this priority**: 证券行业监管明确要求操作留痕，审计日志是合规刚需，非可选功能。

**Independent Test**: 运行 3 轮问答后，按时间范围查询审计日志，验证每轮包含完整的问题、检索片段列表、回答和评分记录。

**Acceptance Scenarios**:

1. **Given** 系统已运行若干轮问答, **When** 审计人员按时间范围查询, **Then** 返回该时段内所有问答记录，包含用户ID、问题、检索片段列表、排序结果、生成回答、Faithfulness评分、耗时和精确时间戳。
2. **Given** 审计日志数据量增长, **When** 按用户ID或话题关键词查询, **Then** 查询响应时间 < 2 秒（10 万条日志规模）。
3. **Given** 某笔问答的 Faithfulness 评分低于阈值, **When** 审计人员回溯该记录, **Then** 能看到检索、排序、生成各环节的详细信息，定位哪个环节导致评分下降。

---

### User Story 3 - 全链路可观测性 (Priority: P1)

运维人员需要实时监控 RAG 管道各环节的运行状态，及时发现性能瓶颈和异常。当 Faithfulness Evaluator 兜底触发时，需要定位故障环节（召回不足？排序偏差？生成幻觉？）。

**Why this priority**: 可观测性保障系统可运营、可诊断。P1 而非 P0 是因为审计日志已覆盖事后追溯，可观测性更多面向运维和优化。

**Independent Test**: 运行一轮问答后，在 OTLP 后端查看完整 Trace，验证各环节 Span 齐全、Metrics 数据点存在。

**Acceptance Scenarios**:

1. **Given** 系统正常运行, **When** 用户发起一轮问答, **Then** OpenTelemetry Trace 覆盖以下环节且各环节 Span 有独立耗时：文档导入、嵌入计算、向量检索、全文检索、Reranker、LLM 生成、Faithfulness 评估。
2. **Given** RAG 管道出现性能问题, **When** 运维人员查看 Metrics, **Then** 可见 QPS、延迟分布（p50/p95/p99）、缓存命中率、Early Exit 命中率、Faithfulness 通过率等关键指标。
3. **Given** Faithfulness 评分低于阈值的问答, **When** 通过 Trace ID 追溯, **Then** 能从 Trace 链路中识别出故障环节（检索召回不足、排序偏差或生成偏差）。

---

### User Story 4 - 会话管理 (Priority: P1)

用户通过 CLI 管理多个并行会话：创建新会话、恢复历史会话、列出所有会话、删除不再需要的会话。支持在多个研究课题间切换，各自保留独立上下文。

**Why this priority**: 会话管理是多轮对话的基础支撑，但优先级低于对话本身的上下文连续性。

**Independent Test**: 创建两个不同话题的会话，交替提问验证上下文隔离，然后删除其中一个会话。

**Acceptance Scenarios**:

1. **Given** 用户在研究两个不同课题, **When** 分别创建两个会话并各自提问, **Then** 两个会话的上下文完全隔离，互不干扰。
2. **Given** 用户有多个历史会话, **When** 执行列出会话命令, **Then** 显示每个会话的ID、创建时间、最近活跃时间和消息轮数。
3. **Given** 用户删除某个会话, **When** 尝试恢复该会话, **Then** 系统返回"会话不存在"的明确提示，且该会话的审计日志不受影响（审计日志不可删除）。

---

### Edge Cases

- 会话中连续提问导致上下文窗口截断时，系统应保留最近 5 轮 / 4000 tokens，丢弃更早的对话，且不破坏 LLM 提示的连贯性。
- 服务在写入审计日志过程中崩溃（如 PostgreSQL 不可达），应将日志暂存本地队列并在恢复后补写，不丢失任何一笔问答记录。
- OpenTelemetry Collector 不可达时，系统问答功能不受影响，Trace/Metrics 降级为本地日志输出，不阻塞主流程。
- 用户在极短时间（<100ms）内对同一会话连续提交多个问题，应串行处理避免并发写入冲突。
- 审计日志查询涉及大量数据时（>10 万条），应支持分页返回，避免一次性加载导致内存溢出。

## Requirements *(mandatory)*

### Functional Requirements

**多轮对话**

- **FR-001**: 系统 MUST 支持会话内多轮对话，LLM 提示中包含历史对话上下文，使追问能引用前文内容。
- **FR-002**: 系统 MUST 使用持久化存储（Redis 主 + PostgreSQL 备用）保存会话记忆，替代 InMemoryChatMessageStore，支持服务重启后恢复。
- **FR-003**: 系统 MUST 实施 Sliding Window 截断策略：保留最近 5 轮对话且总 Token 数不超过 4000，超出部分从最早轮次丢弃。
- **FR-004**: 系统 MUST 支持会话管理操作：创建新会话、恢复已有会话、列出所有会话、删除会话。
- **FR-005**: 系统 MUST 确保不同会话的上下文完全隔离，一个会话的历史不影响另一个会话的问答结果。

**审计日志**

- **FR-006**: 系统 MUST 记录每笔问答的完整审计信息：用户ID、会话ID、问题全文、检索片段列表（含来源和相似度分数）、排序后的片段列表、LLM 生成的回答全文、Faithfulness 评分、各环节耗时明细、精确时间戳。
- **FR-007**: 系统 MUST 支持按时间范围、用户ID、话题关键词查询审计日志，并支持分页返回。
- **FR-008**: 审计日志 MUST 不可删除或篡改（满足证券行业操作留痕监管要求），仅有追加和查询权限。
- **FR-009**: 系统 MUST 在审计日志存储不可达时暂存日志到本地队列，待存储恢复后补写，确保不丢失任何一笔记录。

**OpenTelemetry 可观测性**

- **FR-010**: 系统 MUST 为 RAG 管道全链路输出 OpenTelemetry Trace，覆盖文档导入、嵌入计算、向量检索、全文检索、Reranker、LLM 生成、Faithfulness 评估各环节，每个环节为独立 Span。
- **FR-011**: 系统 MUST 输出以下 OpenTelemetry Metrics：QPS（每秒问答数）、延迟分布（p50/p95/p99）、向量检索缓存命中率、Early Exit 命中率、Faithfulness 通过率。
- **FR-012**: 系统 MUST 支持可插拔的 OTLP 后端：可配置接入 Langfuse / Arize Phoenix / 自建 OTLP 收集器，且不影响核心问答功能。
- **FR-013**: 当 OTLP 后端不可达时，MUST 降级为本地日志输出，不阻塞主流程。

**跨功能需求**

- **FR-014**: 审计日志和 Trace 通过会话ID和请求ID关联，支持从审计日志追溯到完整的 Trace 链路。
- **FR-015**: 会话创建/恢复时 MUST 从持久化存储加载历史消息，并与检索到的知识库片段合并构成 LLM 提示。

### Key Entities

- **ChatSession**: 多轮对话会话。属性：会话ID、创建时间、最近活跃时间、消息轮数、存储位置标识（Redis/PostgreSQL）。
- **ChatMessage**: 单条对话消息。属性：消息ID、所属会话ID、角色（user/assistant/system）、内容、Token 数量、时间戳。
- **AuditLog**: 问答审计记录。属性：记录ID、用户ID、会话ID、问题全文、检索片段列表（含来源/分数/排序位置）、排序后片段列表、LLM 回答全文、Faithfulness 评分、各环节耗时明细、总耗时、时间戳。**不可删除、不可篡改**。
- **TraceSpan**: OpenTelemetry Trace 的 Span 实体。属性：TraceID、SpanID、父SpanID、环节名称、开始时间、结束时间、状态、属性键值对。此实体由 OTLP 后端管理，系统只负责产出。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 多轮对话利用率 ≥ 60%（即 ≥ 60% 的用户在首次提问后会继续在同一会话中追问，基于使用日志统计）。
- **SC-002**: Sliding Window 截断后，追问回答的上下文相关性评分 ≥ 4.0/5.0（基于人工评估 30 组追问场景）。
- **SC-003**: 服务重启后会话恢复成功率 100%（所有已持久化会话均可恢复，无数据丢失）。
- **SC-004**: 每笔问答的审计日志完整记录率 100%（无一遗漏，含各环节明细）。
- **SC-005**: 审计日志查询响应时间 < 2 秒（10 万条日志规模，带时间范围和用户过滤条件）。
- **SC-006**: OpenTelemetry Trace 覆盖 RAG 管道全部 7 个环节（文档导入、嵌入计算、向量检索、全文检索、Reranker、LLM 生成、Faithfulness 评估），每环节 Span 齐全率 100%。
- **SC-007**: OTLP 后端不可达时，系统问答功能零影响（响应时间差异 < 5%，成功率差异 0%）。
- **SC-008**: 五项关键 Metrics（QPS、延迟分布、缓存命中率、Early Exit 命中率、Faithfulness 通过率）均可通过 OTLP 后端查询到实时数值。

## Assumptions

- 目标用户仍为证券行业从业人员，M4 不引入 Web 前端，仅 CLI 交互。
- 会话存储以 Redis 为主、PostgreSQL 为备用，不需要两者同时写入（主备切换场景在运维层面处理）。
- Sliding Window 参数（5 轮 / 4000 tokens）为初始默认值，后续可根据实际效果调整，但 M4 阶段使用固定值。
- 审计日志的"不可删除、不可篡改"通过 PostgreSQL 表级权限控制实现，不引入区块链等额外技术。
- 用户ID 在 M4 阶段通过 CLI 参数传入，不涉及 JWT 认证（M5 范围）。
- 话题关键词查询基于 PostgreSQL 全文检索，不需要独立的搜索引擎。
- OpenTelemetry 后端选择（Langfuse / Arize Phoenix / 自建）在部署阶段配置，M4 代码层面只确保 OTLP 协议兼容。
- 10 万条审计日志规模为 M4 阶段的性能基线，更大规模可能需要分库或归档策略，但 M4 不涉及。
- 审计日志本地暂存队列在服务崩溃场景下允许丢失（尽力交付），仅在存储不可达的优雅降级场景保证不丢。
- 多轮对话的 Token 计数使用 LLM Tokenizer 的近似估算，不需要精确到与 LLM 计费一致的精度。
- 会话管理操作（列出、删除）为 CLI 命令，不需要 REST API（M5 范围）。