# Research: Securities Clearing & Technology Knowledge QA Agent (M1)

**Branch**: `001-securities-qa-agent` | **Date**: 2026-06-09 | **Spec**: [spec.md](./spec.md)

## DEC-001: Haystack 2.x Pipeline 架构

**Decision**: 使用 Haystack 2.x 标准 Pipeline 架构

**Indexing Pipeline**: `FileTypeRouter → Converter → DocumentJoiner → DocumentCleaner → HierarchicalDocumentSplitter → Embedder → DocumentWriter`

**Query Pipeline**: `TextEmbedder → EmbeddingRetriever(chunk_store) → AutoMergingRetriever(parent_store, threshold) → PromptBuilder → Generator`

**Rationale**: Haystack 2.x 官方推荐模式，FileTypeRouter 按 MIME 类型分流，各路输出经 DocumentJoiner 合流。AutoMergingRetriever 自动合并小块命中的父文档，天然实现"小块检索、大块喂 LLM"。

**Alternatives Considered**:
- 自定义小块→大块映射（需要手写连接逻辑，维护成本高）
- SentenceWindowRetriever（扩展相邻窗口，但不做智能合并）
- LangChain ParentDocumentRetriever（与 Haystack 生态不兼容）

## DEC-002: TurboQuantDocumentStore 集成

**Decision**: TurboQuantDocumentStore 作为 Haystack DocumentStore 替代 InMemoryDocumentStore

**关键 API**:
- 构造: `TurboQuantDocumentStore(dim=None, bit_width=4, embedding_similarity_function="cosine")`
- 写入: `write_documents(docs, policy=DuplicatePolicy.SKIP)`
- 检索: `embedding_retrieval(query_embedding, top_k, filters)`
- 过滤: `filter_documents(filters)` — 完整支持 Haystack filter DSL，内部转为 allowlist 前置过滤
- 持久化: `save_to_disk(folder_path)` / `load_from_disk(folder_path)`
- 元数据: `count_documents_by_filter()`, `get_metadata_field_unique_values()`, `update_by_filter()`
- Pipeline 序列化: `to_dict()` / `from_dict()`

**Rationale**: turbovec 是 drop-in replacement，API 与 InMemoryDocumentStore 一致。量化后内存节省 80%+，SIMD 内核检索速度优于 FAISS PQ。allowlist 前置过滤保证 top_k 结果数不受影响。

**Alternatives Considered**:
- InMemoryDocumentStore（全精度，内存占用大）
- FAISS DocumentStore（需额外依赖，API 不同）

## DEC-003: 分层文档检索策略

**Decision**: 使用 HierarchicalDocumentSplitter + AutoMergingRetriever

**实现方案**:
1. 索引时: `HierarchicalDocumentSplitter(block_sizes={500, 100})` 创建两层文档树
   - level 1: 500 字大块（section 级）→ 存入 parent_store
   - level 2: 100 字小块（paragraph 级）→ 存入 chunk_store（做嵌入/检索）
2. 检索时: EmbeddingRetriever 从 chunk_store 检索小块 → AutoMergingRetriever 检查同一父节点命中比例 → 超过 threshold(0.5) 则返回大块

**Rationale**: 证券清算条文有层级结构（章→节→条→款），分层检索天然匹配。AutoMergingRetriever 是 Haystack 内置组件，无需自定义。小块保证检索精度，大块保证 LLM 上下文完整性。

**Alternatives Considered**:
- 双 Store 方案（chunk_store + parent_store 各用不同 DocumentStore）— 推荐，职责清晰
- 单 Store + meta 关联（小块存 parent_id，检索后 filter 取大块）— 可行但查询次数翻倍
- SentenceWindowRetriever（扩展前后 N 句窗口）— 简单但窗口固定，不智能合并

## DEC-004: PDF 表格提取方案

**Decision**: Docling（docling-haystack 集成包）作为主 PDF 转换器，PyPDFToDocument 作为 fallback

**Rationale**: 证券清算规则文档表格多、结构复杂。Docling 基于 DocLayNet 深度学习模型做版面分析，表格识别和结构化输出质量远超传统方案。支持 `ExportType.DOC_CHUNKS` 直接输出分层 chunk，可与 HierarchicalDocumentSplitter 配合。

**配置**:
- Docling: `DoclingConverter(export_type=ExportType.DOC_CHUNKS)` — 含表格的 PDF
- PyPDFToDocument: `PyPDFToDocument(extraction_mode=PyPDFExtractionMode.LAYOUT)` — fallback，纯文本 PDF
- OCR 支持: `PdfPipelineOptions(do_ocr=True)` — 处理扫描件

**Alternatives Considered**:
- PyMuPDFConverter（提取质量好但非核心组件，表格能力弱于 Docling）
- pdfplumber（表格好但无 Haystack 集成，需自定义 Converter）
- Apache Tika（覆盖广但需 Java 服务，部署重）

## DEC-005: Word/Markdown/HTML 解析

**Decision**:
- Word: `DOCXToDocument(table_format=DOCXTableFormat.MARKDOWN, link_format=DOCXLinkFormat.MARKDOWN)` — Haystack 原生组件
- Markdown: `MarkdownToDocument` → `DocumentCleaner` → `HierarchicalDocumentSplitter`
- HTML: `HTMLToDocument` → `DocumentCleaner` → `HierarchicalDocumentSplitter`

**Rationale**: Haystack 原生组件，DOCXToDocument 支持表格 Markdown 输出，MarkdownToDocument 保留标题层级。

## DEC-006: 嵌入模型选择

**Decision**: BGE-M3 作为默认嵌入模型，通过内部 OpenAI 兼容 API 部署

**配置**:
- 模型: BAAI/bge-m3（1024 维，中英双语，支持 8192 token 长文本）
- 集成: `OpenAIDocumentEmbedder(api_base_url="内部API地址", model="bge-m3")`
- 备选: `SentenceTransformersDocumentEmbedder(model="BAAI/bge-m3")` — 本地 GPU 部署

**Rationale**: BGE-M3 是当前中文检索综合最优模型，支持密集+稀疏+多粒度检索。通过 `OpenAIDocumentEmbedder` 的 `api_base_url` 参数对接内部 API，满足数据不出境要求。

**Alternatives Considered**:
- BGE-large-zh-v1.5（中文专项更强但仅密集检索）
- bce-embedding-base_v1（金融领域优化但通用性弱）
- GTE-multilingual-base（多语言均衡但中文弱于 BGE）

## DEC-007: CLI 架构

**Decision**: 使用 typer 构建 CLI，支持单次命令和交互式会话两种模式

**结构**:
```
qa ask "问题"          # 单次问答
qa index ./docs        # 构建索引
qa add ./new_docs      # 增量添加文档
qa remove --source X   # 按来源删除文档
qa status              # 查看知识库状态
qa chat                # 交互式会话模式
qa config              # 配置管理
```

**Rationale**: typer 基于类型注解，自动生成帮助文档和参数验证。单次命令适合脚本化调用，交互式会话适合探索性使用。Pipeline 对象在交互模式下常驻内存避免重复加载。

**Alternatives Considered**:
- click（功能类似但 API 更冗长）
- argparse（标准库但无自动帮助文档生成）
- 纯 Python REPL（无结构化命令）

## DEC-008: 元数据过滤实现

**Decision**: 使用 Haystack filter DSL + turbovec allowlist 前置过滤

**配置**:
```python
filters = {
    "operator": "AND",
    "conditions": [
        {"field": "meta.source", "operator": "==", "value": "CSDC"},
        {"field": "meta.effective_date", "operator": ">=", "value": "2024-01-01"},
    ],
}
results = store.embedding_retrieval(query_embedding=q, top_k=5, filters=filters)
```

**Rationale**: turbovec 的 `embedding_retrieval` 中 filter 先解析为 allowlist 再送入 SIMD 内核，保证 top_k 结果数不受 filter 排除影响。与 Haystack 原生 filter DSL 完全兼容。

## DEC-009: LLM API 失败处理

**Decision**: 直接报错，不做缓存重试或降级到本地模型

**处理策略**:
- LLM/Embedding API 不可用 → 返回明确错误"服务暂时不可用，请稍后重试"
- API 调用超时（超过可配置阈值）→ 返回超时错误
- 错误信息记录到日志，包含请求 ID 和时间戳便于排查

**Rationale**: 证券行业对准确性要求极高，缓存或降级可能导致不一致的回答。明确报错让用户知道服务不可用，比返回可能错误的信息更负责任。

## DEC-010: 项目结构

**Decision**: 单项目结构（Python CLI 应用）

```
src/
├── qa/                    # 主包
│   ├── __init__.py
│   ├── cli/               # CLI 入口
│   │   ├── __init__.py
│   │   ├── main.py         # typer app 定义
│   │   ├── ask.py          # 单次问答命令
│   │   ├── chat.py         # 交互式会话
│   │   ├── index.py        # 索引管理命令
│   │   └── config.py       # 配置管理
│   ├── pipelines/          # Haystack Pipeline 定义
│   │   ├── __init__.py
│   │   ├── indexing.py      # 文档索引 Pipeline
│   │   ├── querying.py     # 问答 Pipeline
│   │   └── components/     # 自定义组件
│   │       ├── __init__.py
│   │       └── hierarchical_store.py  # 分层检索组件
│   ├── stores/             # Document Store 封装
│   │   ├── __init__.py
│   │   └── turbovec_store.py
│   ├── converters/         # 文档转换器适配
│   │   ├── __init__.py
│   │   └── docling_converter.py
│   └── config/             # 配置
│       ├── __init__.py
│       └── settings.py     # Pydantic Settings
tests/
├── unit/
├── integration/
└── contract/
```

**Rationale**: 单项目 CLI 应用，按功能模块组织。pipelines/ 集中管理 Haystack Pipeline 定义，stores/ 封装 turbovec 适配层，converters/ 处理文档格式转换。