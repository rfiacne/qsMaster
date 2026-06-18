# CLI Contracts: Securities QA Agent (M1)

**Branch**: `001-securities-qa-agent` | **Date**: 2026-06-09 | **Spec**: [spec.md](../spec.md)

## CLI Command Schema

### `qa ask`

单次问答命令。

```bash
qa ask "问题文本" [OPTIONS]

OPTIONS:
  --top-k INT          检索返回的最大文档数 (默认: 5)
  --filter JSON        元数据过滤条件 (默认: 无)
  --no-answer          仅返回检索结果，不调用 LLM 生成
  --format FORMAT      输出格式: text | json (默认: text)

STDOUT:
  text 格式: 回答文本 + 引用来源列表
  json 格式: QueryResult JSON 对象

EXIT CODES:
  0  成功
  1  知识库为空
  2  LLM API 不可用
  3  API 超时
  4  输入验证失败
```

### `qa index`

构建或增量更新索引。

```bash
qa index PATH [OPTIONS]

PATH:              文件或目录路径（支持 pdf/docx/md/html）

OPTIONS:
  --source TEXT       来源机构（必需元数据）
  --category TEXT     文档类别（必需元数据）
  --effective-date DATE  生效日期 YYYY-MM-DD（必需元数据）
  --version TEXT      版本号（可选元数据）
  --tags LIST         标签列表（可选元数据）
  --description TEXT  文档摘要（可选元数据）
  --rebuild           全量重建索引（默认增量）
  --bit-width INT     turbovec 量化宽度: 4 或 2 (默认: 4)

EXIT CODES:
  0  成功
  1  缺少必需元数据
  2  文件格式不支持
  3  索引构建失败
  4  文件不存在
```

### `qa remove`

删除文档。

```bash
qa remove [OPTIONS]

OPTIONS:
  --source TEXT       按来源机构删除
  --category TEXT     按类别删除
  --ids LIST          按文档 ID 列表删除
  --all               清空整个索引

EXIT CODES:
  0  成功
  1  没有匹配文档
```

### `qa status`

查询知识库状态。

```bash
qa status [OPTIONS]

OPTIONS:
  --format FORMAT     输出格式: text | json (默认: text)

STDOUT:
  IndexStatus 对象 (文档数/片段数/索引大小/最近更新/量化宽度/向量维度)
```

### `qa chat`

交互式会话模式。

```bash
qa chat [OPTIONS]

OPTIONS:
  --top-k INT          检索返回的最大文档数 (默认: 5)
  --history INT        保留最近 N 轮对话上下文 (默认: 5, M1 为单轮)

INTERACTIVE:
  > 问题文本          问答
  > /status           查看知识库状态
  > /filter JSON       设置元数据过滤
  > /clear             清除会话上下文
  > /quit              退出

EXIT:
  Ctrl+C 或 /quit
```

### `qa config`

配置管理。

```bash
qa config [COMMAND] [OPTIONS]

COMMANDS:
  show                显示当前配置
  set KEY VALUE       设置配置项
  init                初始化配置文件

OPTIONS:
  --config PATH       配置文件路径 (默认: ~/.qa/config.yaml)
```

## Configuration Schema

```yaml
# ~/.qa/config.yaml
llm:
  api_base_url: "http://internal-llm:8000/v1"  # 内部 OpenAI 兼容 API 地址
  model: "gpt-4"                                  # LLM 模型名称
  api_key_env: "INTERNAL_API_KEY"                 # 环境变量名（不直接存储密钥）
  timeout_seconds: 30                             # API 超时阈值

embedding:
  api_base_url: "http://internal-llm:8000/v1"    # 嵌入 API 地址（可与 LLM 共用）
  model: "bge-m3"                                  # 嵌入模型名称
  dimensions: 1024                                 # 向量维度
  timeout_seconds: 30

vector_store:
  type: "turbovec"                                 # 向量库类型
  bit_width: 4                                     # 量化宽度 (4 或 2)
  similarity_function: "cosine"                    # 相似度函数
  persist_path: "./data/index"                     # 持久化路径

retrieval:
  top_k: 5                                         # 默认检索数量
  auto_merge_threshold: 0.5                         # AutoMergingRetriever 阈值
  block_sizes: [500, 100]                          # 分层分块大小（字数）

indexing:
  batch_size: 100                                  # 批量索引大小
  ocr_enabled: false                               # 是否启用 OCR（扫描件）
```

## Error Response Schema

所有 CLI 命令的错误输出遵循统一格式：

```
JSON (stderr):
{
  "error": "ERROR_CODE",
  "message": "人类可读的错误描述",
  "details": {}  // 可选的额外上下文
}
```

**ERROR_CODES**:
- `EMPTY_INDEX`: 知识库为空
- `LLM_UNAVAILABLE`: LLM API 不可用
- `EMBEDDING_UNAVAILABLE`: Embedding API 不可用
- `RETRIEVAL_UNAVAILABLE`: 检索后端不可用（区别于嵌入服务故障）
- `API_TIMEOUT`: API 调用超时
- `VALIDATION_ERROR`: 输入验证失败（缺少必需元数据等）
- `INDEX_ERROR`: 索引构建失败
- `FILE_NOT_FOUND`: 文件不存在
- `UNSUPPORTED_FORMAT`: 文件格式不支持