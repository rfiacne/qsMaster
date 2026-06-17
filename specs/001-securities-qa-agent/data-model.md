# Data Model: Securities QA Agent (M1)

**Branch**: `001-securities-qa-agent` | **Date**: 2026-06-09 | **Spec**: [spec.md](./spec.md)

## Entities

### KnowledgeDocument

证券清算与技术知识的原始文档。

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | str | auto | Haystack Document UUID |
| content | str | yes | 文档原始文本内容 |
| meta.source | str | **yes** | 来源机构（如 "CSDC", "SSE", "SZSE"） |
| meta.category | str | **yes** | 文档类别（如 "clearing_rule", "tech_manual", "regulation"） |
| meta.effective_date | str (ISO date) | **yes** | 生效日期 |
| meta.version | str | no | 版本号 |
| meta.tags | list[str] | no | 标签列表 |
| meta.description | str | no | 文档摘要 |
| meta.file_path | str | auto | 源文件路径 |
| meta.file_type | str | auto | 文件格式（pdf/docx/md/html） |
| meta.indexed_at | str (ISO datetime) | auto | 索引时间戳 |

**Validation Rules**:
- 缺少 source/category/effective_date 任一字段 → 拒绝导入，返回明确错误列出缺失字段
- effective_date MUST 为有效 ISO 日期格式 (YYYY-MM-DD)
- source MUST 为预定义机构列表之一（可配置扩展）

**Lifecycle**: 导入 → 索引 → 可检索；可删除（级联删除所有子 chunk）；可更新（DuplicatePolicy.OVERWRITE）

### DocumentChunk

按分层策略切分的文档片段。小块（段落级）用于检索，大块（章节级）用于 LLM 上下文。

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | str | auto | Haystack Document UUID |
| content | str | yes | 片段文本内容 |
| embedding | list[float] | auto | 预计算嵌入向量（由 Embedder 生成） |
| meta.parent_id | str | yes | 父级块文档 ID（顶层为 None/root_id） |
| meta.children_ids | list[str] | conditional | 子块 ID 列表（仅非叶节点有） |
| meta.level | int | yes | 层级深度（0=根文档, 1=section, 2=paragraph） |
| meta.source_id | str | yes | 所属原始文档 ID |
| meta.split_id | str | auto | 分块唯一标识符 |
| meta.split_idx_start | int | auto | 在原文中的字符起始位置 |

**Hierarchy**:
```
Level 0: KnowledgeDocument (根文档，全文)
Level 1: Section chunks (大块，~500 字，作为 LLM 上下文)
Level 2: Paragraph chunks (小块，~100 字，用于向量检索)
```

**Validation Rules**:
- Level 2（叶节点）MUST 有 embedding
- Level 1（section 块）MAY 有 embedding（取决于是否也参与检索）
- parent_id MUST 指向有效的同层上级文档
- 同一 source_id 下的分块 MUST 保持字符位置连续性

**Lifecycle**: 由 HierarchicalDocumentSplitter 创建，随父文档导入；随父文档删除而级联删除

### VectorIndex

基于 TurboQuant 的向量索引配置。

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| dim | int \| None | None | 向量维度（None 时从首次写入推断） |
| bit_width | int | 4 | 量化宽度（4 或 2） |
| embedding_similarity_function | str | "cosine" | 相似度函数（"cosine" 或 "dot_product"） |
| persist_path | str \| None | None | 磁盘持久化路径 |

**Validation Rules**:
- bit_width MUST 为 2 或 4
- dim 一旦设定（首次写入时推断或显式指定）MUST NOT 变更
- embedding_similarity_function MUST 为 "cosine" 或 "dot_product"
- persist_path 目录 MUST 可写

**Lifecycle**: 构造 → 写入文档 → 可持久化(save_to_disk) / 可加载(load_from_disk) → 可增量更新

### QueryResult

一次问答的完整输出。

| Field | Type | Description |
|-------|------|-------------|
| question | str | 用户原始问题 |
| answer | str | LLM 生成的回答 |
| sources | list[SourceRef] | 引用来源列表 |
| retrieval_time_ms | float | 检索阶段耗时（毫秒） |
| generation_time_ms | float | LLM 生成耗时（毫秒） |
| total_time_ms | float | 端到端总耗时（毫秒） |
| api_error | str \| None | 错误信息（正常时为 None） |

### SourceRef

引用来源，QueryResult 的子结构。

| Field | Type | Description |
|-------|------|-------------|
| document_id | str | 源文档 ID |
| file_name | str | 源文件名 |
| content | str | 引用的文本片段 |
| source | str | 来源机构 |
| category | str | 文档类别 |
| effective_date | str | 生效日期 |
| score | float | 检索相似度分数 |

### IndexStatus

知识库状态查询结果。

| Field | Type | Description |
|-------|------|-------------|
| document_count | int | 已索引文档数量 |
| chunk_count | int | 已索引文档片段数量 |
| index_size_bytes | int | 索引内存占用（字节） |
| last_updated | str (ISO datetime) | 最近更新时间 |
| bit_width | int | 当前量化宽度 |
| dim | int \| None | 向量维度 |

## Relationships

```
KnowledgeDocument (1) ──< DocumentChunk (N)
    └─ source_id → KnowledgeDocument.id

DocumentChunk (1) ──< DocumentChunk (N)  [parent-child]
    └─ parent_id → DocumentChunk.id (上级)
    └─ children_ids → [DocumentChunk.id] (下级)

KnowledgeDocument → VectorIndex
    └─ written to via DocumentWriter

QueryResult ──< SourceRef (N)
    └─ references DocumentChunk via document_id
```

## State Transitions

### KnowledgeDocument
```
[新文档] → 验证元数据(必需字段) → [已验证] → 解析+分块+嵌入 → [已索引] → 可检索
                                    ↓ 失败
                               [被拒绝] (缺少必需元数据)
```

### QueryResult
```
[用户问题] → 检索 → 生成 → [成功回答]
              ↓        ↓
         [检索失败]  [生成失败] → [错误返回]
```