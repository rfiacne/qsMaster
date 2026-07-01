# API 参考文档

## 基本信息

| 项目 | 值 |
|------|-----|
| 基础 URL | `http://host:8001/api/v1` |
| 认证方式 | `X-API-Key` 请求头（可选，配置 `server.api_keys` 后启用） |
| 限流 | `X-RateLimit-Limit` / `X-RateLimit-Remaining` 响应头 |
| 请求 ID | `X-Request-ID` 请求头（可选）/ 响应头 |
| 内容类型 | `application/json` |

---

## 认证

API 密钥认证通过 `X-API-Key` 请求头实现。当配置 `server.api_keys` 为空列表时，跳过鉴权（向后兼容）。

**绕过鉴权的路径**（只读安全操作）：
- `GET /api/v1/qa/health`
- `GET /api/v1/qa/status`
- `GET /api/v1/qa/metrics`
- `GET /docs`
- `GET /openapi.json`

**限流**：基于内存滑动窗口（per API key），默认 60 RPM。超限返回 `429 Too Many Requests`。

---

## 端点清单

总计 **21 个端点**，分布在 6 个路由模块。

### 1. QA 问答

#### `POST /api/v1/qa/ask`

单次问答（非流式 JSON 响应）。

**请求体**：
```json
{
  "question": "沪深交易所 T+1 清算流程是什么？",
  "top_k": 5,
  "filters": null,
  "no_llm": false,
  "session_id": ""
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | 是 | 用户问题 |
| `top_k` | int | 否 | 检索数量（默认 5） |
| `filters` | object | 否 | 检索过滤条件 |
| `no_llm` | bool | 否 | 仅检索不生成 LLM 回答 |
| `session_id` | string | 否 | 会话 ID（续接多轮对话） |

**响应**：
```json
{
  "answer": "...",
  "sources": [
    {
      "title": "文件名.pdf",
      "content": "片段原文...",
      "score": 0.85,
      "url": "来源标识"
    }
  ],
  "faithfulness": {
    "score": 0.92,
    "status": "PASS",
    "details": [
      {"claim": "声明1", "supported": true, "evidence": "..."},
      {"claim": "声明2", "supported": true, "evidence": "..."}
    ]
  },
  "session_id": "abc123",
  "rewritten_question": "归一化后的问题"
}
```

#### `POST /api/v1/qa/ask/stream`

流式问答（SSE — Server-Sent Events）。

**请求体**：同上。

**事件流**：
```
event: session_id
data: {"session_id": "abc123"}

event: token
data: "回答片段"

event: sources
data: [{"title": "...", "content": "...", "score": 0.85}]

event: faithfulness
data: {"score": 0.92, "status": "PASS"}

event: done
data: ""
```

#### `POST /api/v1/qa/search`

仅检索（不调用 LLM 生成回答）。

**请求体**：
```json
{
  "query": "清算流程",
  "top_k": 20
}
```

**响应**：返回来源列表，同 `sources` 数组。

#### `GET /api/v1/qa/status`

知识库状态。

**响应**：
```json
{
  "document_count": 42,
  "chunk_count": 1523,
  "index_size_bytes": 52428800,
  "last_updated": "2026-06-01T12:00:00",
  "bit_width": 4,
  "dim": 1024,
  "persist_path": "./data/index"
}
```

---

### 2. 文档上传

#### `POST /api/v1/qa/upload`

上传并索引文档。

**请求体**：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `files` | file[] | 是 | 待上传文件（支持 PDF/DOCX/XLSX/MD/HTML） |
| `source` | string | 是 | 来源（如 CSDC, SSE, SZSE） |
| `category` | string | 否 | 分类（如 clearing_rule, settlement_procedure） |
| `effective_date` | string | 否 | 生效日期（YYYY-MM-DD） |
| `meta` | string | 否 | 前端 JSON 字符串（兼容格式，自动解析） |

**响应**：
```json
{
  "files_count": 3,
  "documents_written": 3,
  "documents_skipped": 0,
  "segments": 25,
  "parents": 5,
  "errors": [],
  "time_ms": 15234,
  "skipped_files": []
}
```

**文件去重**：自动计算文件 MD5，跳过已索引的重复文件，记录在 `skipped_files` 字段。

---

### 3. 会话管理

#### `GET /api/v1/qa/sessions`

列出最近会话。

**参数**：`limit`（int，默认 20）

#### `POST /api/v1/qa/sessions`

创建新会话。

**请求体**：
```json
{
  "title": "会话标题"
}
```

#### `GET /api/v1/qa/sessions/{session_id}`

获取会话详情（含完整对话历史）。

#### `DELETE /api/v1/qa/sessions/{session_id}`

删除会话。

#### `POST /api/v1/qa/sessions/{session_id}/clear`

清空会话历史（保留会话本身）。

---

### 4. 标准答案管理

#### `GET /api/v1/qa/answers`

标准答案列表。

**参数**：`category`, `keyword`, `status`, `page`, `page_size`

#### `POST /api/v1/qa/answers`

新增标准答案。

**请求体**：
```json
{
  "question": "问题",
  "answer": "标准答案",
  "category": "分类",
  "source": "来源",
  "match_strategy": "both"
}
```

`match_strategy`: `exact` | `fuzzy` | `both`

#### `DELETE /api/v1/qa/answers/{answer_id}`

删除标准答案。

#### `PATCH /api/v1/qa/answers/{answer_id}/status`

启用/禁用标准答案。

**请求体**：
```json
{
  "status": "enabled"
}
```

`status`: `enabled` | `disabled`

#### `POST /api/v1/qa/answers/{answer_id}/aliases`

添加别名问题。

**请求体**：
```json
{
  "alias_question": "别名问题",
  "similarity_score": 0.95
}
```

---

### 5. 审核队列

#### `GET /api/v1/qa/reviews`

审核队列列表。

**参数**：`status`, `page`, `page_size`

#### `POST /api/v1/qa/reviews/{item_id}/label`

标注审核项。

**请求体**：
```json
{
  "label": "correct",
  "reviewer": "web",
  "comment": "确认无误"
}
```

`label`: `correct` | `partial` | `incorrect`

标注为 `correct` 时自动加入标准答案库（auto-convert）。

#### `GET /api/v1/qa/reviews/stats`

审核统计。

---

### 6. 运维探针

#### `GET /api/v1/qa/health`

轻量健康检查。

**响应**：`{"status": "ok", "version": "0.1.0"}`

#### `GET /api/v1/qa/ready`

深度依赖检查（向量库 / 嵌入 / LLM）。

**200 正常**：返回各组件状态。
**503 降级**：返回异常详情。

#### `GET /api/v1/qa/metrics`

可观测性指标（QPS / 延迟百分位 / Faithfulness 记录 / 缓存命中率）。

---

## 数据模型

### QueryResult

| 字段 | 类型 | 说明 |
|------|------|------|
| `answer` | string | LLM 生成的回答 |
| `sources` | SourceRef[] | 知识库来源列表 |
| `faithfulness` | FaithfulnessReport | 防幻觉校验报告 |
| `session_id` | string | 会话 ID |
| `rewritten_question` | string | 改写后的问题（M6） |

### SourceRef

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | 文档标题/文件名 |
| `content` | string | 片段原文 |
| `score` | float | 归一化 RRF 分数（M6） |
| `url` | string | 来源标识 |

### FaithfulnessReport

| 字段 | 类型 | 说明 |
|------|------|------|
| `score` | float | 整体支撑比例（0~1） |
| `status` | string | `PASS` / `PARTIAL` / `FAIL` |
| `details` | FaithfulnessDetail[] | 逐声明校验详情 |

### FaithfulnessDetail

| 字段 | 类型 | 说明 |
|------|------|------|
| `claim` | string | LLM 生成的声明 |
| `supported` | bool | 是否有文档支撑 |
| `evidence` | string | 支撑证据片段 |

---

## 响应头

| 头字段 | 说明 |
|--------|------|
| `X-API-Key` | 请求认证（可选） |
| `X-RateLimit-Limit` | 每分钟请求限制上限 |
| `X-RateLimit-Remaining` | 本窗口剩余请求数 |
| `X-Request-ID` | 请求追踪 ID（可调用方传入） |
| `Retry-After` | 429 超限后建议等待秒数 |

## 错误响应

| HTTP 状态码 | 说明 |
|-------------|------|
| 400 | 请求参数错误 |
| 401 | API Key 无效或缺失 |
| 404 | 资源不存在 |
| 429 | 请求超限 |
| 500 | 服务内部错误 |

错误响应体：
```json
{
  "error": "错误描述",
  "code": "ERROR_CODE"
}
```
