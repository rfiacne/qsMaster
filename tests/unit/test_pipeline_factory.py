"""
Pipeline 组件工厂与 wiring 测试（M6 review finding #1）

验证 build_query_components / build_bm25_index 构建出的组件
能被 QueryPipeline 接受并流入 HybridRetriever，
避免"配置存在但运行时未接入"的回归。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from qa.pipelines.factory import build_bm25_index, build_query_components
from qa.pipelines.querying import QueryPipeline


@dataclass
class FakeDocument:
    id: str | None = None
    content: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


class FakeChunkStore:
    """最小 chunk_store 替身，支持 filter_documents 与 count"""

    def __init__(self, docs):
        self._docs = docs

    def filter_documents(self, filters=None):
        return list(self._docs)

    def __len__(self):
        return len(self._docs)


class FakeStoreManager:
    def __init__(self, docs):
        self._docs = docs
        self.chunk_store = FakeChunkStore(docs)
        self._version = "test-v1"

    @property
    def store_version(self):
        return self._version

    def count_chunks(self):
        return len(self._docs)

    def retrieve(self, query_embedding, top_k=10, filters=None):
        return sorted(self._docs, key=lambda d: -d.score)[:top_k]


def _make_docs(n=3):
    return [
        FakeDocument(
            id=f"d{i}",
            content=f"证券清算文档 {i} 内容",
            meta={"file_path": f"d{i}.txt"},
            score=0.9 - i * 0.1,
        )
        for i in range(n)
    ]


def test_build_bm25_index_returns_built_index():
    """build_bm25_index 在 use_hybrid + 非空库时返回可用索引。"""
    # 直接用真实 BM25 逻辑，仅 mock settings 与 store
    fake_settings = type(
        "S",
        (),
        {"retrieval": type("R", (), {"use_hybrid": True})()},
    )()
    store = FakeStoreManager(_make_docs())
    idx = build_bm25_index(fake_settings, store)
    assert idx is not None
    assert idx.is_built
    assert idx.total_docs == 3


def test_build_bm25_index_none_when_hybrid_disabled():
    fake_settings = type(
        "S",
        (),
        {"retrieval": type("R", (), {"use_hybrid": False})()},
    )()
    store = FakeStoreManager(_make_docs())
    assert build_bm25_index(fake_settings, store) is None


def test_build_bm25_index_none_when_empty():
    fake_settings = type(
        "S",
        (),
        {"retrieval": type("R", (), {"use_hybrid": True})()},
    )()
    store = FakeStoreManager([])
    assert build_bm25_index(fake_settings, store) is None


def test_query_pipeline_forwards_bm25_index_to_retriever():
    """QueryPipeline 必须把 bm25_index 透传给 HybridRetriever。"""
    store = FakeStoreManager(_make_docs())
    fake_settings = type(
        "S",
        (),
        {"retrieval": type("R", (), {"use_hybrid": True})()},
    )()
    bm25 = build_bm25_index(fake_settings, store)

    pipeline = QueryPipeline(
        store_manager=store,
        use_hybrid=True,
        bm25_index=bm25,
    )
    assert pipeline.hybrid_retriever is not None
    assert pipeline.hybrid_retriever.bm25_index is bm25
    assert pipeline.hybrid_retriever.bm25_index.is_built


def test_query_pipeline_retriever_is_none_when_not_hybrid():
    store = FakeStoreManager(_make_docs())
    pipeline = QueryPipeline(store_manager=store, use_hybrid=False, bm25_index=None)
    assert pipeline.hybrid_retriever is None


def test_build_query_components_returns_three_tuple():
    """工厂返回 (rewriter, cache, bm25) 三元组，未启用项为 None。"""
    store = FakeStoreManager(_make_docs())
    # 用 patch 让 QueryRewriter/QueryCache 构造不依赖外部资源
    with patch("qa.pipelines.factory.get_settings") as gs:
        # 构造一个最小 settings 对象
        fake = type(
            "S",
            (),
            {
                "retrieval": type("R", (), {"use_hybrid": True})(),
                "query_rewrite": type(
                    "QR",
                    (),
                    {
                        "enabled": False,
                        "term_map_path": "./data/term_map.json",
                        "model": "",
                        "timeout_seconds": 3.0,
                    },
                )(),
                "query_cache": type(
                    "QC",
                    (),
                    {
                        "enabled": False,
                        "max_size": 256,
                        "ttl_seconds": 300.0,
                    },
                )(),
            },
        )()
        gs.return_value = fake
        rewriter, cache, bm25 = build_query_components(fake, store)
    assert rewriter is None  # query_rewrite.enabled=False
    assert cache is None  # query_cache.enabled=False
    assert bm25 is not None  # use_hybrid=True + 非空库
