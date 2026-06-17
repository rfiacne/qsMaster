# Securities QA Agent

证券清算与技术知识问答系统（RAG）。基于 Haystack 2.x + turbovec，支持多格式文档导入和自然语言问答。

## 快速开始

```bash
# 一键安装（全量）
pip install -e ".[dev,ocr,pdf,local]"

# 或分步安装
pip install -e ".[dev]"         # 核心 + 开发工具
pip install -e ".[ocr]"         # OCR（PaddleOCR，扫描件+图片）
pip install -e ".[pdf]"         # PDF 增强（OpenDataLoader）
pip install -e ".[local]"       # 本地嵌入模型（jina nano）

# 初始化配置
cp config.example.yaml ~/.qa/config.yaml
# 编辑 ~/.qa/config.yaml 填入 API 地址
set INTERNAL_API_KEY="your-api-key"

# 启动服务
start.bat          # 交互式菜单
# 或
.venv\Scripts\python -m qa.api.server   # 直接启动 API
```

## 架构

```text
文档 (PDF/DOCX/DOC/XLSX/XLS/MD/HTML/TXT/JPG/PNG)
    │
    ▼
FileRouter → Converters → Embedding → turbovec
    │
    ═══════════════════════════════
    │
用户问题 → HybridRetriever(BM25 + 向量) → LLM → 回答
```

### 嵌入模式

| 模式 | 说明 | 配置 |
|------|------|------|
| 🌐 远程 API | 调用 OpenAI 兼容 API（默认） | `backend: api` |
| 💻 本地模型 | jina-embeddings-v5-text-nano（768维） | `backend: local` |
| 🔀 自动回退 | 先试远程，不通则切本地 | `backend: auto` |

## 命令

| 命令 | 说明 |
|------|------|
| `qa ask "问题"` | 单次问答 |
| `qa chat` | 交互式会话 |
| `qa index <path>` | 构建索引 |
| `qa status` | 知识库状态 |
| `qa remove` | 删除文档 |
| `qa config show` | 查看配置 |

## API 服务

```bash
# 启动 API 服务（前端 + 后端一体）
.venv\Scripts\python -m qa.api.server
# 浏览器打开 http://127.0.0.1:8001

# REST 接口
POST /api/v1/qa/ask       # 问答
POST /api/v1/qa/search    # 知识库检索
POST /api/v1/qa/upload    # 上传文档
GET  /api/v1/qa/status    # 知识库状态
```

## 支持的文档格式

| 格式 | 扩展名 | 解析方式 |
|------|--------|----------|
| PDF | `.pdf` | Docling → OpenDataLoader → PaddleOCR → PyMuPDF |
| Word | `.docx` `.doc` | python-docx（含表格） |
| Excel | `.xlsx` `.xls` | openpyxl / xlrd（每 sheet 转 Markdown 表格） |
| Markdown | `.md` `.markdown` | 直接读取，保留标题层级 |
| HTML | `.html` `.htm` | BeautifulSoup 正文提取 |
| 文本 | `.txt` | UTF-8/GBK 自动检测 |
| 图片 | `.jpg` `.jpeg` `.png` | PaddleOCR 文字识别 |

## 一键脚本

| 脚本 | 用途 |
|------|------|
| `setup.bat` | 首次初始化（venv + 依赖 + 配置） |
| `start.bat` | 启动菜单（API / CLI / Web） |
| `clean-db.bat` | 清空知识库索引 |
| `qa.bat` | 命令行直接调用 |

## 依赖分组

```bash
pip install -e ".[dev]"         # 核心 + 开发（pytest/ruff/mypy）
pip install -e ".[ocr]"         # OCR 识别（PaddleOCR）
pip install -e ".[pdf]"         # PDF 增强（OpenDataLoader，需 Java 11+）
pip install -e ".[local]"       # 本地嵌入模型（sentence-transformers + torch）
```

或使用 requirements 文件：

```bash
pip install -r requirements.txt          # 核心
pip install -r requirements-all.txt      # 全量
pip install -r requirements-ocr.txt      # OCR
pip install -r requirements-pdf.txt      # PDF 增强
pip install -r requirements-local.txt    # 本地模型
```

## 里程碑

- **M1** ✅ 核心问答闭环（当前）
- **M2** 🔄 混合检索 + Early Exit
- **M3** 标准答案 + 审核
- **M4** 多轮对话 + 可观测性
- **M5** Web 前端 + API 网关
