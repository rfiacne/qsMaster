# Pipeline Contracts: Securities QA Agent (M1)

**Branch**: `001-securities-qa-agent` | **Date**: 2026-06-09 | **Spec**: [spec.md](../spec.md)

## Indexing Pipeline Contract

### Input

```python
{
    "sources": [str],           # 文件路径列表
    "meta": {                   # 元数据（必需字段必须提供）
        "source": str,          # 必需：来源机构
        "category": str,       # 必需：文档类别
        "effective_date": str, # 必需：生效日期 (YYYY-MM-DD)
        "version": str | None, # 可选：版本号
        "tags": list | None,   # 可选：标签列表
        "description": str | None  # 可选：摘要
    }
}
```

### Output

```python
{
    "documents_written": int,   # 实际写入文档数
    "documents_skipped": int,   # 跳过的文档数（DuplicatePolicy.SKIP）
    "errors": [str],            # 错误信息列表
    "index_status": IndexStatus  # 索引状态
}
```

### Pipeline Components

```
FileTypeRouter
  ├─ pdf → DoclingConverter(export_type=DOC_CHUNKS)
  ├─ docx → DOCXToDocument(table_format=MARKDOWN)
  ├─ md → MarkdownToDocument()
  └─ html → HTMLToDocument()
        ↓
  DocumentJoiner
        ↓
  DocumentCleaner
        ↓
  HierarchicalDocumentSplitter(block_sizes={500, 100})
        ↓  (split into parent + child chunks)
  DocumentEmbedder(model="bge-m3", api_base=internal_api)
        ↓
  DocumentWriter(document_store=turbovec_store)
```

### Validation Rules

- 每个 Document 进入 Pipeline 前必须通过元数据验证（source/category/effective_date）
- 缺少必需元数据的文档 MUST 被拒绝，不影响其他文档处理
- PDF 中无 OCR 文本的扫描图片 MUST 跳过并记录 WARNING 日志
- 特殊字符（清算代码、证书编号）MUST 保留不丢失

## Query Pipeline Contract

### Input

```python
{
    "question": str,                # 用户问题
    "top_k": int = 5,               # 检索返回的最大文档数
    "filters": dict | None = None,  # 元数据过滤条件
    "no_llm": bool = False           # 仅检索不生成
}
```

### Output (成功)

```python
{
    "question": str,
    "answer": str,                   # LLM 生成的回答
    "sources": [SourceRef],          # 引用来源列表
    "retrieval_time_ms": float,
    "generation_time_ms": float,
    "total_time_ms": float,
    "api_error": None
}
```

### Output (失败)

```python
{
    "question": str,
    "answer": None,
    "sources": [],
    "retrieval_time_ms": float | None,
    "generation_time_ms": None,
    "total_time_ms": float,
    "api_error": "LLM_UNAVAILABLE" | "API_TIMEOUT" | "EMBEDDING_UNAVAILABLE"
}
```

### Pipeline Components

```
TextEmbedder(model="bge-m3", api_base=internal_api)
        ↓
EmbeddingRetriever(document_store=chunk_store, top_k=5, filters=filters)
        ↓
AutoMergingRetriever(document_store=parent_store, threshold=0.5)
        ↓
PromptBuilder(template=RAG_PROMPT_TEMPLATE)
        ↓
Generator(model="gpt-4", api_base=internal_api)
```

### RAG Prompt Template

```
你是一个证券清算与技术领域的专业问答助手。请基于以下检索到的文档片段回答用户的问题。

要求：
1. 只基于检索到的文档内容回答，不要编造信息
2. 如果文档内容不足以回答问题，明确说明"根据现有知识库内容，无法完整回答此问题"
3. 在回答中标注引用来源，格式为 [来源:文件名]
4. 对于涉及金额、日期、规则编号的具体信息，确保准确无误

检索到的文档片段：
{% for doc in documents %}
---
[来源:{{ doc.meta.file_path }}]
{{ doc.content }}
---
{% endfor %}

用户问题: {{ question }}
```

### Error Handling Rules

| Error | Code | User Message | Action |
|-------|------|-------------|--------|
| 知识库为空 | EMPTY_INDEX | "知识库尚未建立，请先导入文档" | 返回提示，不调用 LLM |
| LLM API 不可用 | LLM_UNAVAILABLE | "服务暂时不可用，请稍后重试" | 返回错误，不缓存不降级 |
| Embedding API 不可用 | EMBEDDING_UNAVAILABLE | "服务暂时不可用，请稍后重试" | 返回错误，不缓存不降级 |
| API 超时 | API_TIMEOUT | "请求超时，请稍后重试" | 返回错误，可配置超时阈值 |
| 检索无结果 | 无 | 基于空上下文生成"无法找到相关信息" | 正常流程，LLM 生成兜底回答 |