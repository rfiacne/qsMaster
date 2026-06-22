"""
TurboQuantDocumentStore 封装层

提供双 store 管理（chunk_store + parent_store），
支持分层检索（小块检索 → 大块上下文）。

API 完全兼容 Haystack 2.x DocumentStore 接口，
作为 InMemoryDocumentStore 的 drop-in replacement。
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haystack import Document
from haystack.document_stores.types import DuplicatePolicy

logger = logging.getLogger(__name__)


@dataclass
class IndexStatus:
    """知识库状态快照"""

    document_count: int = 0
    chunk_count: int = 0
    parent_count: int = 0
    index_size_bytes: int = 0
    last_updated: str = ""
    bit_width: int = 4
    dim: int | None = None
    persist_path: str = ""


class StoreManager:
    """向量存储管理器

    统一管理 chunk_store（小块，用于检索）和 parent_store（大块，用于 LLM 上下文）。

    用法:
        manager = StoreManager(config)
        manager.initialize()
        manager.write_documents(docs)
        results = manager.retrieve(query_embedding, top_k=5)
    """

    def __init__(
        self,
        dim: int | None = None,
        bit_width: int = 4,
        similarity_function: str = "cosine",
        persist_path: str = "./data/index",
    ):
        self.dim = dim
        self.bit_width = bit_width
        self.similarity_function = similarity_function
        self.persist_path = persist_path

        self.chunk_store: Any = None  # TurboQuantDocumentStore
        self.parent_store: Any = None  # TurboQuantDocumentStore
        self._initialized = False
        self._version: str = ""

    @property
    def store_version(self) -> str:
        """chunk store 版本号

        基于 chunk 数量 + 索引文件 mtime 的哈希值。
        索引内容变化（增/删文档、重建）时版本号变化，未变更时稳定。
        用于 BM25 持久化索引的失效判定。
        """
        if not self._initialized:
            self.initialize()
        if not self._version:
            self._version = self._compute_version()
        return self._version

    def _compute_version(self) -> str:
        """计算当前索引版本哈希"""
        chunk_count = self.count_chunks()
        # 收集索引文件的 mtime
        mtimes: list[float] = []
        persist = Path(self.persist_path)
        if persist.exists():
            for tvim_file in persist.rglob("index.tvim"):
                try:
                    mtimes.append(tvim_file.stat().st_mtime)
                except OSError:
                    pass
        # 组合输入
        raw = f"{chunk_count}:{sorted(mtimes)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def invalidate_version(self) -> None:
        """强制下一次读取时重新计算版本号"""
        self._version = ""

    def initialize(self) -> None:
        """初始化向量存储（加载或新建）"""
        from turbovec.haystack import TurboQuantDocumentStore

        persist = Path(self.persist_path)
        chunk_path = persist / "chunks"
        parent_path = persist / "parents"

        # 尝试从磁盘加载
        if chunk_path.exists() and (chunk_path / "index.tvim").exists():
            logger.info(f"从磁盘加载 chunk_store: {chunk_path}")
            try:
                self.chunk_store = TurboQuantDocumentStore.load_from_disk(str(chunk_path))
            except Exception as e:
                logger.error(f"chunk_store 加载失败（将新建）: {e}")
                self.chunk_store = TurboQuantDocumentStore(
                    dim=self.dim,
                    bit_width=self.bit_width,
                    embedding_similarity_function=self.similarity_function,
                )
        else:
            self.chunk_store = TurboQuantDocumentStore(
                dim=self.dim,
                bit_width=self.bit_width,
                embedding_similarity_function=self.similarity_function,
            )

        if parent_path.exists() and (parent_path / "index.tvim").exists():
            logger.info(f"从磁盘加载 parent_store: {parent_path}")
            try:
                self.parent_store = TurboQuantDocumentStore.load_from_disk(str(parent_path))
            except Exception as e:
                logger.error(f"parent_store 加载失败（将新建）: {e}")
                self.parent_store = TurboQuantDocumentStore(
                    dim=self.dim,
                    bit_width=self.bit_width,
                    embedding_similarity_function=self.similarity_function,
                )
        else:
            self.parent_store = TurboQuantDocumentStore(
                dim=self.dim,
                bit_width=self.bit_width,
                embedding_similarity_function=self.similarity_function,
            )

        self._initialized = True
        logger.info(
            f"StoreManager 初始化完成: "
            f"bit_width={self.bit_width}, "
            f"persist_path={self.persist_path}"
        )

    # ─── 写入 ───────────────────────────────────────────────

    def write_chunks(
        self,
        documents: list[Document],
        policy: DuplicatePolicy = DuplicatePolicy.SKIP,
    ) -> int:
        """写入小块文档到 chunk_store（用于向量检索）"""
        if not self._initialized:
            self.initialize()
        result = self.chunk_store.write_documents(documents, policy=policy)
        self.invalidate_version()
        return result

    def write_parents(
        self,
        documents: list[Document],
        policy: DuplicatePolicy = DuplicatePolicy.SKIP,
    ) -> int:
        """写入大块文档到 parent_store（用于 LLM 上下文）"""
        if not self._initialized:
            self.initialize()
        result = self.parent_store.write_documents(documents, policy=policy)
        self.invalidate_version()
        return result

    # ─── 检索 ───────────────────────────────────────────────

    def retrieve(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[Document]:
        """从 chunk_store 检索最相似的文档"""
        if not self._initialized:
            self.initialize()
        return self.chunk_store.embedding_retrieval(
            query_embedding=query_embedding,
            top_k=top_k,
            filters=filters,
        )

    def get_parent_docs(
        self, parent_ids: list[str]
    ) -> list[Document]:
        """根据 parent_id 批量获取父级文档"""
        if not self._initialized:
            self.initialize()

        # parent_store 使用 filter_documents 按 id 过滤
        filters = {
            "operator": "OR",
            "conditions": [
                {"field": "id", "operator": "==", "value": pid}
                for pid in parent_ids
            ],
        }
        return self.parent_store.filter_documents(filters=filters)

    def get_docs_by_ids(self, ids: list[str]) -> list[Document]:
        """按文档 ID 检索（跨两个 store 查找）"""
        if not self._initialized:
            self.initialize()

        id_filter = {
            "operator": "OR",
            "conditions": [
                {"field": "id", "operator": "==", "value": doc_id}
                for doc_id in ids
            ],
        }

        # 先从 chunk_store 查
        results = self.chunk_store.filter_documents(filters=id_filter)
        # 再从 parent_store 补充
        parent_results = self.parent_store.filter_documents(filters=id_filter)

        # 去重
        seen = {d.id for d in results}
        for d in parent_results:
            if d.id not in seen:
                results.append(d)
                seen.add(d.id)

        return results

    # ─── 删除 ───────────────────────────────────────────────

    def delete_documents(self, ids: list[str]) -> int:
        """删除指定文档（两个 store 同时删除）"""
        if not self._initialized:
            self.initialize()
        count = self.chunk_store.delete_documents(ids)
        count += self.parent_store.delete_documents(ids)
        if count > 0:
            self.invalidate_version()
        return count

    def delete_by_filter(self, filters: dict) -> int:
        """按过滤条件删除文档"""
        if not self._initialized:
            self.initialize()
        count = self.chunk_store.delete_by_filter(filters)
        count += self.parent_store.delete_by_filter(filters)
        if count > 0:
            self.invalidate_version()
        return count

    def delete_all(self) -> int:
        """清空全部索引"""
        if not self._initialized:
            self.initialize()
        c1 = self.chunk_store.delete_all_documents()
        c2 = self.parent_store.delete_all_documents()
        if c1 + c2 > 0:
            self.invalidate_version()
        return c1 + c2

    # ─── 持久化 ───────────────────────────────────────────────

    def save(self) -> None:
        """持久化索引到磁盘"""
        if not self._initialized:
            return

        persist = Path(self.persist_path)
        persist.mkdir(parents=True, exist_ok=True)

        chunk_path = persist / "chunks"
        parent_path = persist / "parents"

        chunk_path.mkdir(exist_ok=True)
        parent_path.mkdir(exist_ok=True)

        t0 = time.time()
        self.chunk_store.save_to_disk(str(chunk_path))
        self.parent_store.save_to_disk(str(parent_path))
        elapsed = time.time() - t0
        self.invalidate_version()

        logger.info(f"索引持久化完成 ({elapsed:.2f}s): {persist}")

    # ─── 状态 ───────────────────────────────────────────────

    def get_status(self) -> IndexStatus:
        """获取知识库状态"""
        if not self._initialized:
            self.initialize()

        chunk_count = self.chunk_store.count_documents()
        parent_count = self.parent_store.count_documents()

        # 估算索引大小
        index_size = self._estimate_size()

        return IndexStatus(
            document_count=parent_count,  # 原始文档数 ≈ parent_count
            chunk_count=chunk_count,
            parent_count=parent_count,
            index_size_bytes=index_size,
            last_updated=time.strftime("%Y-%m-%dT%H:%M:%S"),
            bit_width=self.bit_width,
            dim=self.dim,
            persist_path=self.persist_path,
        )

    def _estimate_size(self) -> int:
        """估算索引占用磁盘大小"""
        persist = Path(self.persist_path)
        total = 0
        if persist.exists():
            for root, _dirs, files in os.walk(persist):
                for f in files:
                    fp = Path(root) / f
                    try:
                        total += fp.stat().st_size
                    except OSError:
                        pass
        return total

    def count_documents(self) -> int:
        """文档总数"""
        if not self._initialized:
            return 0
        return self.parent_store.count_documents()

    def count_chunks(self) -> int:
        """文档片段总数"""
        if not self._initialized:
            return 0
        return self.chunk_store.count_documents()

    def get_unique_metadata_values(self, field: str) -> list[Any]:
        """获取元数据字段的唯一值列表"""
        if not self._initialized:
            self.initialize()
        return self.parent_store.get_metadata_field_unique_values(field)


# ─── Store 工厂 ───────────────────────────────────────────────


def create_store_manager(
    dim: int | None = None,
    bit_width: int = 4,
    similarity_function: str = "cosine",
    persist_path: str = "./data/index",
) -> StoreManager:
    """创建并初始化 StoreManager"""
    manager = StoreManager(
        dim=dim,
        bit_width=bit_width,
        similarity_function=similarity_function,
        persist_path=persist_path,
    )
    manager.initialize()
    return manager
