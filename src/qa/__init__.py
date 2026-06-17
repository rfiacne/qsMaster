#!/usr/bin/env python3
"""
Securities QA Agent — 证券清算与技术知识问答系统

基于 Haystack 2.x + turbovec 的 RAG 应用，支持多格式文档导入、
向量检索增强生成（RAG）、引用溯源和 CLI 交互。

能力范围：
- 多格式文档索引（PDF / DOCX / Markdown / HTML）
- TurboQuant 量化向量存储（8x 内存压缩）
- 分层文档检索（小块检索 + 大块上下文）
- LLM 生成 + 引用溯源
- 单次问答 + 交互式会话 CLI
"""

__version__ = "0.1.0"
