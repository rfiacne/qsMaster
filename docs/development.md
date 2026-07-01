# 开发指南

## 环境搭建

### 前置条件

- Python 3.11+
- pip / uv（推荐 uv 加速依赖安装）
- Git

### 克隆与安装

```bash
git clone <repo-url>
cd qsMaster

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
# .venv\Scripts\activate    # Windows

# 安装核心依赖
pip install -e .

# 安装开发依赖（pytest / ruff / mypy）
pip install -e ".[dev]"

# 或使用 uv（更快）
uv sync
```

### 可选依赖

```bash
pip install -e ".[ocr]"     # OCR 支持（PaddleOCR）
pip install -e ".[pdf]"     # PDF 增强（OpenDataLoader，需 Java 11+）
pip install -e ".[local]"   # 本地嵌入（sentence-transformers + torch）
```

### 配置

```bash
cp config.example.yaml ~/.qa/config.yaml
# 编辑 ~/.qa/config.yaml 填入 API 地址和密钥
```

---

## 开发工作流

### 代码质量

```bash
# Lint（自动修复）
ruff check --fix

# 格式化
ruff format

# 类型检查
mypy

# 全量
ruff check --fix && ruff format && mypy
```

### 测试

```bash
# 单元测试（mock，无需外部依赖）
python -m pytest tests/unit/ -v

# 集成测试（需要 testfile 目录下的真实文档）
python -m pytest tests/integration/ -v

# 契约测试
python -m pytest tests/contract/ -v

# 全量测试
python -m pytest

# 带覆盖率
python -m pytest --cov=src/qa --cov-report=html
```

### 运行

```bash
# CLI 问答
python -m qa.cli.main ask "问题"

# 启动 API 服务
python -m qa.api.server

# 或通过 uvicorn
uvicorn qa.api.server:app --reload

# 使用启动脚本
start.bat
```

---

## 项目约定

### 编码规范

- **语言**：Python 3.11+ 类型注解（PEP 484）
- **文档字符串**：中文简洁描述
- **配置**：Pydantic-Settings（`BaseSettings` + `Field`）
- **测试框架**：pytest
- **异步**：asyncio + FastAPI 异步路由
- **日志**：结构化 JSON 格式（`import logging`）

### 命名规范

| 类别 | 规范 | 示例 |
|------|------|------|
| 类名 | PascalCase | `HybridRetriever`, `QueryPipeline` |
| 函数/方法 | snake_case | `build_query_pipeline`, `embed_query` |
| 模块/文件 | snake_case | `hybrid_retriever.py`, `query_rewriter.py` |
| 配置类 | PascalCase + Config | `RetrievalConfig`, `ServerConfig` |
| 测试文件 | test_*.py | `test_hybrid_retriever.py` |

### 依赖管理

- 核心依赖：`pyproject.toml`（`project.dependencies`）
- 开发依赖：`pyproject.toml`（`[project.optional-dependencies] dev`）
- 锁定版本：`uv.lock`（使用 `uv lock` 更新）
- 备选文件：`requirements.txt` / `requirements-all.txt` / `requirements-{ext}.txt`

---

## 测试分层

| 层级 | 目录 | 依赖 | 用途 |
|------|------|------|------|
| 单元测试 | `tests/unit/` | mock，无外部依赖 | 组件级逻辑验证 |
| 集成测试 | `tests/integration/` | testfile 目录的真实文档 | 管道端到端验证 |
| 契约测试 | `tests/contract/` | API 服务 | API 响应结构验证 |

### 单元测试原则

- 所有外部依赖（LLM、Embedding、文件系统）使用 mock
- 每个组件应有独立测试文件
- 测试覆盖边界条件（空列表、超时、异常回退）

---

## 如何添加新组件

### 1. 新增 Pipeline 组件

1. 在 `src/qa/pipelines/components/` 下创建组件文件
2. 如果组件需要配置，在 `src/qa/config/settings.py` 添加对应的 Config 类
3. 在 `src/qa/pipelines/factory.py` 中注册组件到 pipeline
4. 在 `src/qa/pipelines/querying.py` 中调用组件
5. 添加单元测试 `tests/unit/test_<component>.py`
6. 更新 `config.example.yaml` 添加配置项

### 2. 新增 CLI 命令

1. 在 `src/qa/cli/` 下创建命令文件（Typer）
2. 在 `src/qa/cli/main.py` 中注册子命令
3. 添加单元测试

### 3. 新增 API 路由

1. 在 `src/qa/api/routes/` 下创建路由文件（APIRouter）
2. 在 `src/qa/api/dependencies.py` 注册路由到 app
3. 添加请求模型（Pydantic BaseModel）
4. 测试：契约测试验证响应结构

---

## 调试技巧

### 日志

```bash
# 设置日志级别
export QA_LOG_LEVEL=DEBUG

# 查看结构化日志
python -m qa.cli.main ask "问题" 2>&1 | jq .
```

### 本地测试 LLM

```bash
# 使用 OpenAI 兼容 API 测试
python -c "
from qa.pipelines.prompts import get_generation_prompt
prompt = get_generation_prompt('测试问题', [], '')
print(prompt)
"
```

### 测试 Pipeline 各阶段

```bash
# 仅测试检索（no_llm）
python -m qa.cli.main ask "问题" --no-llm

# 查看配置
python -m qa.cli.main config show
```

---

## 发布

### 版本号

遵循 `M<里程碑>` 格式（如 M1, M2, M6），对应 `CHANGELOG.md` 中的条目。

### 构建与发布

```bash
# 构建 Docker 镜像
docker build -t qa-agent .

# CI 流程
# 1. ruff check --fix && ruff format
# 2. mypy
# 3. pytest
# 4. docker build && docker push
```
