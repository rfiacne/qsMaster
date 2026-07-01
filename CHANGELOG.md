# Changelog

## 0.1.1 — 依赖同步 & 文档修正 (2026-06-30)

### 修复
- **依赖声明**: 新增 `jinja2>=3.1.0`（prompts.py 直接导入），移除 pyproject.toml 重复 `pydantic` 行
- **开发依赖**: `pytest-timeout` 版本对齐至 `>=2.3.0`，新增 `opentelemetry-*` 可选依赖组
- **文档同步**: CLI 命令数修正为 11 个，测试结构补全 contract 层，README 安装命令移除未使用的 `markdown`

## M6 — 相关性增强 & 前端美观性 & 性能优化 (2026-06-10)

### 新增
- **BM25 全量化**: `bm25_index.py` 基于 `store_manager` 全量 chunk 构建 BM25Okapi 并持久化到 `data/bm25/{version}.pkl`
- **RRF k 调优**: `rrf_k` 默认从 60 → 35，提升排序区分度
- **Reranker 默认开启**: 精排组件默认启用
- **分数归一化**: 展示分数从向量余弦改为归一化 RRF 分数
- **查询改写**: `query_rewriter.py`（术语归一化 + 多意图分解）
- **防幻觉强化**: `FaithfulnessConfig.threshold` 默认 0.7；新增 `judge_model` 独立评判模型
- **查询缓存**: `query_cache.py`（LRU + TTL + 索引版本失效）
- **Startup 预热**: 服务启动预加载 store/BM25/embedder

### 前端
- Prose 样式：table/th/td(边框+斑马纹)、h1-h3、blockquote、hr
- marked GFM 配置：`{ breaks: true, gfm: true }`
- 来源卡片：分数百分比/星级、展开全文、文件类型图标
- Faithfulness 可信度徽章（PASS/ PARTIAL/ FAIL）

### 修复
- Faithfulness 低风险跳过条件修正

## M5 — Web 前端 & API 网关 (2026-05-15)

### 新增
- **Web 前端**: `frontend/index.html` 单页应用（搜索/问答/上传/会话管理）
- **CORS 中间件**: 支持配置允许的来源列表
- **API 密钥认证**: `X-API-Key` 请求头鉴权
- **限流器**: 内存滑动窗口（per API key）
- **请求日志**: method/path/latency/status 记录

### 变更
- 路由前缀统一为 `/api/v1/qa/`
- 上传 API 支持文件去重（MD5）

## M4 — 多轮会话 & 可观测性 (2026-04-20)

### 新增
- **SessionStore**: 会话创建、保存、加载、列表
- **多轮对话**: CLI 交互模式 (`qa chat`)
- **Tracing**: OpenTelemetry 兼容追踪
- **Metrics**: 请求计数/延迟百分位/Faithfulness 记录
- **审计日志**: `AuditStore` JSONL 格式，仅追加

## M3 — 审核工作流 & 标准答案库 (2026-03-10)

### 新增
- **审核队列**: `ReviewWorkflow` + `ReviewStore`
- **标准答案库**: `StandardAnswerStore` 精确/模糊匹配
- **Early Exit**: 匹配命中后跳过 LLM 生成
- **自动转换**: 审核标注为 "正确" 时自动加入标准答案库

## M2 — 混合检索 & 防幻觉 (2026-02-01)

### 新增
- **混合检索**: BM25 + 向量检索 + RRF 融合
- **Reranker**: Cross-encoder 精排组件
- **Faithfulness 校验**: 逐 claim 检查文档支撑
- **PostgreSQL 全文检索**: pg_trgm + tsvector
- **本地嵌入模型**: jina-embeddings-v5-text-nano fallback

## M1 — 基础 RAG Pipeline (2026-01-01)

### 新增
- 基础 RAG Pipeline（嵌入 → 检索 → 生成）
- 文档上传与索引（支持 PDF/Word/Excel/HTML/MD）
- Haystack + TurboVec 向量存储
- Auto-merging 检索（chunk → parent 合并）
- OpenAI 兼容 LLM API 集成
- CLI 工具：`qa ask` / `qa index` / `qa config`
- 流式回答（SSE）
- 基础项目结构（配置/中间件/转换器）
