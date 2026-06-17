# Quickstart: Securities QA Agent (M1)

**Branch**: `001-securities-qa-agent` | **Date**: 2026-06-09 | **Spec**: [spec.md](./spec.md)

## Prerequisites

- Python 3.11+
- 内部 OpenAI 兼容 API 地址（LLM + Embedding）
- 磁盘空间 ≥ 10GB（用于索引存储）
- 测试用证券清算文档（PDF/Markdown 格式）

## Setup

```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 配置 API 密钥和端点
cp config.example.yaml ~/.qa/config.yaml
# 编辑 config.yaml，填入内部 API 地址

# 3. 设置环境变量
export INTERNAL_API_KEY="your-api-key"

# 4. 验证安装
qa config show
```

## Validation Scenarios

### V1: 文档索引构建

**验证目标**: PDF 和 Markdown 文档能被正确解析、分块、嵌入并写入 turbovec 索引。

```bash
# 索引单份 PDF 文档
qa index ./tests/fixtures/clearing_rule.pdf \
  --source CSDC \
  --category clearing_rule \
  --effective-date 2024-01-01

# 索引 Markdown 文档
qa index ./tests/fixtures/tech_manual.md \
  --source SSE \
  --category tech_manual \
  --effective-date 2024-06-01

# 索引目录（批量）
qa index ./tests/fixtures/ \
  --source SSE \
  --category mixed \
  --effective-date 2024-01-01

# 验证索引状态
qa status
# 预期输出: document_count > 0, chunk_count > 0
```

**Expected Outcome**: 所有文档成功索引，`qa status` 显示非零文档数和片段数。

### V2: 元数据验证拒绝

**验证目标**: 缺少必需元数据的文档被拒绝导入。

```bash
# 缺少 --source 参数
qa index ./tests/fixtures/clearing_rule.pdf \
  --category clearing_rule \
  --effective-date 2024-01-01
# 预期: 错误退出，提示 "缺少必需元数据: source"
```

**Expected Outcome**: 命令返回错误码 1，明确列出缺失字段。

### V3: 单次问答

**验证目标**: 问答链路端到端工作（检索 + 生成 + 引用）。

```bash
# 单次问答
qa ask "沪深交易所 T+1 清算流程是什么？"
# 预期输出: 包含引用来源的专业回答

# 带过滤器
qa ask "银行间债券市场清算方式" --filter '{"operator": "AND", "conditions": [{"field": "meta.source", "operator": "==", "value": "CSDC"}]}'
# 预期输出: 仅来自 CSDC 来源的回答

# 仅检索
qa ask "CSDC 的作用" --no-answer
# 预期输出: 仅返回检索片段，不调用 LLM
```

**Expected Outcome**: 回答包含引用来源标注，超出范围的问题返回"无法回答"提示。

### V4: 交互式会话

**验证目标**: 交互模式可正常使用。

```bash
qa chat
> 沪深交易所清算时间
# 预期: 返回回答
> /status
# 预期: 显示知识库状态
> /quit
```

**Expected Outcome**: 交互流畅，状态查询正确。

### V5: 索引持久化

**验证目标**: 索引可持续化和加载。

```bash
# 索引文档
qa index ./tests/fixtures/ --source CSDC --category clearing_rule --effective-date 2024-01-01

# 验证索引大小
qa status --format json | jq '.index_size_bytes'
# 记录索引大小

# 重启后加载索引（通过配置 persist_path）
qa ask "清算流程"
# 预期: 无需重新索引即可回答
```

**Expected Outcome**: 持久化和加载均 < 60 秒，检索结果一致。

### V6: API 失败处理

**验证目标**: LLM/Embedding API 不可用时正确返回错误。

```bash
# 模拟 API 不可用（设置错误 API 地址）
export INTERNAL_API_BASE="http://nonexistent:9999/v1"
qa ask "测试问题"
# 预期输出: "服务暂时不可用，请稍后重试"

# 模拟超时（设置极短超时）
qa config set llm.timeout_seconds 1
qa ask "测试问题"
# 预期输出: "请求超时，请稍后重试"
```

**Expected Outcome**: 错误信息清晰，退出码非零。

## Performance Baselines

| Metric | Target | Validation |
|--------|--------|-----------|
| 单次问答端到端 p95 | < 5s | `time qa ask "..."` |
| 向量检索 p95 (10万文档) | < 500ms | 基准测试脚本 |
| turbovec 内存占用 vs InMemory | ≤ 20% | 对比测试 |
| 索引持久化+加载 | < 60s | `time` 测量 |
| 1000 篇文档全量索引 | 成功完成 | 批量索引测试 |

## References

- [Data Model](./data-model.md) — 实体定义和关系
- [CLI Contracts](./contracts/cli-contracts.md) — 命令行接口规范
- [Pipeline Contracts](./contracts/pipeline-contracts.md) — Pipeline 输入输出规范
- [Research](./research.md) — 技术决策和替代方案