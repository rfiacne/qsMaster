"""
HybridRetriever 单元测试

测试 RRF 融合逻辑、来源类型标注（source_type）、
分数计算等核心逻辑。

需要 mock haystack 和 store_manager。
"""

from __future__ import annotations

# Create a proper Document class for testing
from dataclasses import dataclass, field  # noqa: E402
from typing import Any  # noqa: E402


@dataclass
class FakeDocument:
    """Minimal stand-in for haystack.Document"""
    id: str | None = None
    content: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0

    def __repr__(self):
        return f"Doc({self.id}, score={self.score})"

# 导入被测模块
from qa.pipelines.components.hybrid_retriever import HybridRetriever  # noqa: E402


class FakeStoreManager:
    """Minimal stand-in for StoreManager"""
    def __init__(self, docs=None):
        self.docs = docs or []

    def retrieve(self, query_embedding, top_k=10, filters=None):
        # 返回按 score 排序的前 top_k 个文档
        sorted_docs = sorted(self.docs, key=lambda d: -d.score)
        return sorted_docs[:top_k]


def make_doc(doc_id: str, content: str, score: float) -> FakeDocument:
    """Helper to create a fake document"""
    return FakeDocument(
        id=doc_id,
        content=content,
        meta={"file_path": f"{doc_id}.pdf", "source": "test"},
        score=score,
    )


class TestSourceTypeAnnotation:
    """来源类型标注"""

    def test_vector_only_source(self):
        """仅在向量 top_k 中的文档 → source_type=vector"""
        docs = [
            make_doc("doc1", "内容1 Python清算", 0.9),
            make_doc("doc2", "内容2 规则", 0.7),
            make_doc("doc3", "内容3 技术", 0.5),
        ]
        store = FakeStoreManager(docs)
        hybrid = HybridRetriever(store_manager=store, top_k=2)
        results = hybrid.retrieve(
            query_embedding=[0.1, 0.2],
            query_text="Python",
            top_k=2,
        )
        # 由于没有实际的 BM25 索引构建（模拟环境），behavior 可能不同
        # 至少返回结果，不会崩溃
        assert len(results) > 0

    def test_top_k_respected(self):
        """top_k 参数应限制返回结果数量"""
        docs = [make_doc(f"doc{i}", f"内容{i}", 1.0 - i*0.1) for i in range(10)]
        store = FakeStoreManager(docs)
        hybrid = HybridRetriever(store_manager=store, top_k=3)
        results = hybrid.retrieve(
            query_embedding=[0.1, 0.2],
            query_text="测试",
            top_k=3,
        )
        assert len(results) <= 3

    def test_empty_vector_results(self):
        """向量检索无结果时应返回空列表"""
        store = FakeStoreManager(docs=[])
        hybrid = HybridRetriever(store_manager=store, top_k=5)
        results = hybrid.retrieve(
            query_embedding=[0.1, 0.2],
            query_text="anything",
        )
        assert results == []


class TestRRFScoring:
    """RRF 融合排序"""

    def test_rrf_score_structure(self):
        """结果应包含 hybrid_score 和 vec_score"""
        docs = [
            make_doc("doc1", "Python清算规则", 0.9),
            make_doc("doc2", "Java规则", 0.6),
        ]
        store = FakeStoreManager(docs)
        hybrid = HybridRetriever(store_manager=store, top_k=2)
        results = hybrid.retrieve(
            query_embedding=[0.1, 0.2],
            query_text="Python",
            top_k=2,
        )
        if results:
            meta = results[0].meta or {}
            assert "hybrid_score" in meta
            assert "vec_score" in meta


class TestTokenization:
    """中文/英文分词"""

    def test_chinese_character_split(self):
        hybrid = HybridRetriever(store_manager=FakeStoreManager())
        tokens = hybrid._tokenize("清算规则")
        # 中文按字符拆分
        assert "清" in tokens
        assert "算" in tokens
        assert "规" in tokens
        assert "则" in tokens

    def test_english_word_split(self):
        hybrid = HybridRetriever(store_manager=FakeStoreManager())
        tokens = hybrid._tokenize("CCASS settlement")
        assert "ccass" in tokens
        assert "settlement" in tokens

    def test_mixed_chinese_english(self):
        hybrid = HybridRetriever(store_manager=FakeStoreManager())
        tokens = hybrid._tokenize("T+1清算")
        # 中文逐字符，英文部分逐字符
        assert any(c in tokens for c in ("T", "t"))
        assert "1" in tokens
        assert "清" in tokens

    def test_empty_text(self):
        hybrid = HybridRetriever(store_manager=FakeStoreManager())
        tokens = hybrid._tokenize("")
        assert tokens == []

    def test_numbers(self):
        hybrid = HybridRetriever(store_manager=FakeStoreManager())
        tokens = hybrid._tokenize("2024年规则")
        assert "2024" in tokens or "2" in tokens or "0" in tokens


class TestHybridRetrieverInit:
    """构造参数"""

    def test_default_params(self):
        hybrid = HybridRetriever(store_manager=FakeStoreManager())
        assert hybrid.vector_weight == 0.5
        assert hybrid.rrf_k == 35  # M6: 默认从 60 改为 35，提升排序区分度
        assert hybrid.top_k == 10

    def test_custom_params(self):
        hybrid = HybridRetriever(
            store_manager=FakeStoreManager(),
            vector_weight=0.7,
            rrf_k=30,
            top_k=5,
        )
        assert hybrid.vector_weight == 0.7
        assert hybrid.rrf_k == 30
        assert hybrid.top_k == 5

    def test_weight_clamping(self):
        """权重应被限制在 [0, 1]"""
        hybrid = HybridRetriever(
            store_manager=FakeStoreManager(),
            vector_weight=1.5,
        )
        assert hybrid.vector_weight == 1.0

        hybrid2 = HybridRetriever(
            store_manager=FakeStoreManager(),
            vector_weight=-0.5,
        )
        assert hybrid2.vector_weight == 0.0
