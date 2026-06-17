# Implementation Plan: Securities QA Agent (M1)

**Branch**: `001-securities-qa-agent` | **Date**: 2026-06-09 | **Spec**: [spec.md](./spec.md)

## Summary

构建证券清算知识问答系统的 M1 最小可用闭环：多格式文档导入（PDF/DOCX/MD/HTML）→ 分层文档检索（HierarchicalDocumentSplitter + AutoMergingRetriever）→ turbovec 向量存储 → LLM 生成 → 引用溯源 → CLI 交互。技术栈为 Python + Haystack 2.x + turbovec + typer CLI。

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: haystack-ai, turbovec[haystack], typer, docling-haystack, pydantic-settings

**Storage**: turbovec（向量索引，磁盘持久化）+ 文件系统（原始文档）

**Testing**: pytest + pytest-cov（目标 80% 覆盖率）

**Target Platform**: Linux 服务器 / Docker 容器

**Project Type**: CLI 应用（library + CLI front-end）

**Performance Goals**: 问答端到端 p95 < 5s，向量检索 p95 < 500ms（10万文档），turbovec 内存 ≤ InMemory 的 20%

**Constraints**: 数据不出境（内部 OpenAI 兼容 API），单轮问答，M1 不含 Web 前端

**Scale/Scope**: 1000-10000 篇文档初始规模，后续可扩展至 10 万级

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Code Quality | ✅ PASS | 遵循命名规范、不可变数据、SOLID/DRY/YAGNI |
| II. Test-First | ✅ PASS | TDD 流程，80% 覆盖率目标，单元/集成/端到端三类测试 |
| III. UX Consistency | ✅ PASS | CLI 统一交互模式（单次命令 + 交互式），错误信息一致 |
| IV. Performance | ✅ PASS | 量化目标已定义（SC-001~008），turbovec 4-bit 压缩 |
| V. Security | ✅ PASS | API 密钥环境变量，输入验证，错误不泄露内部细节 |
| VI. Simplicity | ✅ PASS | 单项目 CLI 结构，M1 最小闭环，YAGNI |
| VII. Documentation | ⚠️ NOTE | 代码完成后需更新 README、CLAUDE.md、变更日志 |
| VIII. CI/CD | ✅ PASS | ruff + mypy + pytest 门禁 |

## Project Structure

### Documentation (this feature)

```text
specs/001-securities-qa-agent/
├── plan.md              # This file
├── research.md           # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── cli-contracts.md  # CLI 接口规范
│   └── pipeline-contracts.md  # Pipeline 输入输出规范
└── tasks.md             # Phase 2 output (NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
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
│   │       └── hierarchical_store.py
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

**Structure Decision**: 单项目 CLI 应用结构。`src/qa/` 按功能模块组织（cli/、pipelines/、stores/、converters/、config/），tests/ 按测试类型分层。参考 research.md DEC-010。

## Complexity Tracking

> No Constitution violations requiring justification.