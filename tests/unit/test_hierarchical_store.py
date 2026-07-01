"""
分层文档存储单元测试 — hierarchical_store.py

覆盖:
  - HierarchicalDocumentSplitter: 分层切分、parent/child 关系、空文档跳过、
    短文档不切分、超长文档多块分割
  - _split_text: 文本分割边界条件
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

# ─── 精细 haystack Document mock ─────────────────────────
# 需要在 hierarchical_store 导入前设置，使 Document() 构造出带属性的对象


class FakeDocument:
    """轻量 Document 替身，保留 id/content/meta 属性"""

    def __init__(self, id="", content="", meta=None):
        self.id = id
        self.content = content
        self.meta = meta or {}


# 设置 haystack mock，使 Document 指向 FakeDocument
_haystack_mock = mock.MagicMock()
_haystack_mock.Document = FakeDocument


# component 装饰器需要返回原类
def _fake_component(cls):
    return cls


_haystack_mock.component = _fake_component
_haystack_mock.component.output_types = lambda **kwargs: lambda cls: cls

sys.modules["haystack"] = _haystack_mock

from qa.pipelines.components.hierarchical_store import HierarchicalDocumentSplitter


class TestHierarchicalDocumentSplitter:
    @pytest.fixture
    def splitter(self):
        return HierarchicalDocumentSplitter(section_size=100, paragraph_size=30, overlap=5)

    def test_empty_documents(self, splitter):
        """空文档列表返回空结果"""
        result = splitter.run(documents=[])
        assert result["parents"] == []
        assert result["chunks"] == []

    def test_blank_content_skipped(self, splitter):
        """空白内容文档被跳过"""
        doc = FakeDocument(id="doc1", content="   ", meta={"source": "test"})
        result = splitter.run(documents=[doc])
        assert result["parents"] == []
        assert result["chunks"] == []

    def test_short_document_single_parent_and_chunk(self, splitter):
        """短文档（小于 section_size）产生 1 个 parent 和 1 个 chunk"""
        doc = FakeDocument(id="doc1", content="短内容", meta={"source": "test"})
        result = splitter.run(documents=[doc])
        assert len(result["parents"]) == 1
        assert len(result["chunks"]) >= 1
        # parent 的 level 为 1
        assert result["parents"][0].meta["level"] == 1
        # chunk 的 level 为 2
        assert result["chunks"][0].meta["level"] == 2

    def test_parent_child_relationship(self, splitter):
        """chunk 的 parent_id 指向对应 parent 的 id"""
        doc = FakeDocument(id="doc1", content="测试内容" * 5, meta={})
        result = splitter.run(documents=[doc])
        parent_ids = {p.id for p in result["parents"]}
        for chunk in result["chunks"]:
            assert chunk.meta["parent_id"] in parent_ids

    def test_children_ids_populated(self, splitter):
        """parent 的 children_ids 包含所有子 chunk 的 id"""
        doc = FakeDocument(id="doc1", content="A" * 200, meta={})
        result = splitter.run(documents=[doc])
        for parent in result["parents"]:
            children_ids = parent.meta.get("children_ids", [])
            for cid in children_ids:
                # 每个 child chunk 的 parent_id 应匹配
                matching = [c for c in result["chunks"] if c.id == cid]
                assert len(matching) == 1
                assert matching[0].meta["parent_id"] == parent.id

    def test_metadata_preserved(self, splitter):
        """原始文档元数据保留在 parent 和 chunk 中"""
        doc = FakeDocument(
            id="doc1",
            content="测试内容",
            meta={"source": "CSDC", "category": "rule"},
        )
        result = splitter.run(documents=[doc])
        for parent in result["parents"]:
            assert parent.meta["source"] == "CSDC"
            assert parent.meta["category"] == "rule"
        for chunk in result["chunks"]:
            assert chunk.meta["source"] == "CSDC"

    def test_long_document_multiple_parents(self, splitter):
        """长文档产生多个 parent 块"""
        doc = FakeDocument(id="doc1", content="A" * 500, meta={})
        result = splitter.run(documents=[doc])
        assert len(result["parents"]) >= 2

    def test_multiple_documents(self, splitter):
        """多文档分别处理"""
        docs = [
            FakeDocument(id="doc1", content="文档一内容" * 5, meta={"doc": "1"}),
            FakeDocument(id="doc2", content="文档二内容" * 5, meta={"doc": "2"}),
        ]
        result = splitter.run(documents=docs)
        # 至少每个文档产生 1 个 parent
        assert len(result["parents"]) >= 2
        source_ids = {p.meta["source_id"] for p in result["parents"]}
        assert "doc1" in source_ids or "doc2" in source_ids


class TestSplitText:
    """_split_text 内部方法的边界测试"""

    @pytest.fixture
    def splitter(self):
        return HierarchicalDocumentSplitter(section_size=50, paragraph_size=20, overlap=5)

    def test_short_text_no_split(self, splitter):
        """短文本不切分"""
        result = splitter._split_text("短文本", 50)
        assert result == ["短文本"]

    def test_exact_size_no_split(self, splitter):
        """恰好等于 chunk_size 不切分"""
        text = "A" * 50
        result = splitter._split_text(text, 50)
        assert result == [text]

    def test_long_text_splits(self, splitter):
        """长文本切分为多块"""
        text = "A" * 200
        result = splitter._split_text(text, 50)
        assert len(result) >= 2
        # 所有块非空
        assert all(len(chunk.strip()) > 0 for chunk in result)

    def test_split_respects_newline_boundary(self, splitter):
        """优先在段落断点处切分"""
        text = "A" * 30 + "\n\n" + "B" * 30 + "\n\n" + "C" * 30
        result = splitter._split_text(text, 50)
        assert len(result) >= 2
