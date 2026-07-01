"""
RAG 提示模板单元测试 — prompts.py

覆盖:
  - format_rag_context: 基本渲染、空文档列表、含对话历史、接地约束提示存在
  - SYSTEM_PROMPT / SYSTEM_PROMPT_WITH_HISTORY 常量验证
"""

from __future__ import annotations

from unittest import mock

from qa.pipelines.prompts import (
    DEFAULT_RAG_TEMPLATE,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_WITH_HISTORY,
    format_rag_context,
)


def _make_doc(content: str, file_path: str = "test.pdf"):
    """创建一个轻量 Document 替身（避免依赖真实 haystack）"""
    doc = mock.MagicMock()
    doc.content = content
    doc.meta = {"file_path": file_path}
    return doc


class TestFormatRagContext:
    def test_basic_rendering(self):
        """基本渲染：包含问题和文档内容"""
        doc = _make_doc("清算规则内容", "clearing_rules.pdf")
        result = format_rag_context("清算流程是什么？", [doc])
        assert "清算流程是什么？" in result
        assert "清算规则内容" in result
        assert "clearing_rules.pdf" in result

    def test_empty_documents(self):
        """空文档列表：不包含文档片段区域"""
        result = format_rag_context("测试问题", [])
        assert "测试问题" in result
        # 无文档片段
        assert (
            "---" not in result.split("检索到的文档片段：")[1]
            if "检索到的文档片段：" in result
            else True
        )

    def test_multiple_documents(self):
        """多文档：每个文档都渲染"""
        docs = [
            _make_doc("内容A", "doc_a.pdf"),
            _make_doc("内容B", "doc_b.pdf"),
        ]
        result = format_rag_context("问题", docs)
        assert "内容A" in result
        assert "内容B" in result
        assert "doc_a.pdf" in result
        assert "doc_b.pdf" in result

    def test_with_conversation_history(self):
        """含对话历史：渲染历史区域"""
        result = format_rag_context(
            "后续问题",
            [_make_doc("参考内容")],
            conversation_history="用户: 之前的问题\n助手: 之前的回答",
        )
        assert "对话历史" in result
        assert "之前的问题" in result
        assert "之前的回答" in result

    def test_without_conversation_history(self):
        """无对话历史：不渲染历史区域"""
        result = format_rag_context("问题", [_make_doc("内容")])
        assert "对话历史" not in result

    def test_grounding_constraint_in_prompt(self):
        """接地约束存在于模板中"""
        assert "根据现有知识库无法回答该问题" in DEFAULT_RAG_TEMPLATE

    def test_citation_format_in_prompt(self):
        """引用格式要求存在于模板中"""
        assert "[来源:文件名]" in DEFAULT_RAG_TEMPLATE


class TestSystemPrompts:
    def test_system_prompt_exists(self):
        """SYSTEM_PROMPT 非空且包含关键约束"""
        assert len(SYSTEM_PROMPT) > 0
        assert "不要编造信息" in SYSTEM_PROMPT

    def test_system_prompt_with_history(self):
        """SYSTEM_PROMPT_WITH_HISTORY 包含历史上下文提示"""
        assert "对话历史" in SYSTEM_PROMPT_WITH_HISTORY

    def test_grounding_constraint_in_system_prompt(self):
        """系统提示包含接地约束"""
        assert "根据现有知识库无法回答该问题" in SYSTEM_PROMPT
