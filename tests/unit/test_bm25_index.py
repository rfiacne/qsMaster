"""
BM25 索引持久化与全量加载测试（M6 review findings #3, #10）

验证：
- save() 原子写入：写入失败不损坏已有文件，旧版本在成功后才清理
- _get_all_chunks 不再有无限循环的备用分页路径
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from qa.pipelines.components.bm25_index import GlobalBM25Index, _get_all_chunks


@dataclass
class FakeDocument:
    id: str | None = None
    content: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


def _make_built_index(tmp_path: Path, docs: list[FakeDocument]) -> GlobalBM25Index:
    """构造一个已构建、可持久化的 GlobalBM25Index（绕过真实 BM25 构建）。"""
    from rank_bm25 import BM25Okapi

    tokenized = [list(d.content) for d in docs]
    idx = GlobalBM25Index(
        index=BM25Okapi(tokenized) if tokenized else BM25Okapi([[""]]),
        documents=[d.content for d in docs],
        doc_metas=[{"id": d.id, "file_path": d.meta.get("file_path", "")} for d in docs],
        version="test-v1",
        persist_dir=str(tmp_path),
    )
    # GlobalBM25Index 内部 _built 标志需要置位
    idx._built = True
    return idx


def test_save_is_atomic_no_corruption_on_failure(tmp_path):
    """pickle.dump 抛异常时，已存在的 .pkl 不被损坏。"""
    docs = [FakeDocument(id="d1", content="证券清算")]
    idx = _make_built_index(tmp_path, docs)

    # 第一次正常保存
    idx.save()
    pkl_path = idx.persist_path()
    assert pkl_path.exists()
    original_bytes = pkl_path.read_bytes()

    # 第二次保存时让 pickle.dump 抛异常
    with patch(
        "qa.pipelines.components.bm25_index.pickle.dump",
        side_effect=RuntimeError("disk full"),
    ):
        with pytest.raises(RuntimeError):
            idx.save()

    # 原文件内容未被破坏（原子替换：临时文件写入失败，原文件未动）
    assert pkl_path.read_bytes() == original_bytes
    # 临时文件不应残留
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_save_cleans_old_versions_only_after_success(tmp_path):
    """旧版本文件在当前版本成功落盘后才被清理。"""
    docs = [FakeDocument(id="d1", content="证券清算")]
    idx = _make_built_index(tmp_path, docs)

    # 预置一个旧版本文件
    old_path = tmp_path / "old-version.pkl"
    old_path.write_bytes(b"old")

    idx.save()
    assert not old_path.exists()  # 旧版本被清理
    assert idx.persist_path().exists()  # 当前版本存在


def test_get_all_chunks_no_infinite_loop_on_failure():
    """filter_documents 抛异常时 _get_all_chunks 直接返回空，不进入无限循环。"""

    class FailingChunkStore:
        def filter_documents(self, filters=None):
            raise RuntimeError("transient store error")

    class FakeStore:
        chunk_store = FailingChunkStore()

    # 应立即返回 []，不挂起
    result = _get_all_chunks(FakeStore())
    assert result == []


def test_get_all_chunks_returns_all_docs():
    docs = [FakeDocument(id=f"d{i}", content=f"内容{i}") for i in range(3)]

    class FakeChunkStore:
        def filter_documents(self, filters=None):
            return list(docs)

    class FakeStore:
        chunk_store = FakeChunkStore()

    result = _get_all_chunks(FakeStore())
    assert len(result) == 3
