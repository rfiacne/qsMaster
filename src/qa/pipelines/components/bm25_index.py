"""
全量 BM25 索引模块 — 构建/持久化/版本化加载/检索

基于 store_manager 全量 chunk 构建 BM25Okapi 索引，
持久化到 data/bm25/{version}.pkl，按 chunk store 版本号失效重建。

用法:
    index = load_or_build(store_manager)
    results = index.retrieve("CCASS 交收流程", top_k=10)
"""

from __future__ import annotations

import logging
import os
import pickle
import re
import threading
import time
from pathlib import Path
from typing import Any

from haystack import Document

logger = logging.getLogger(__name__)

# 默认持久化根目录
DEFAULT_BM25_PERSIST_DIR = "./data/bm25"

# 并发锁：防止 load_or_build() 并发重建导致 pickle 损坏
_build_lock = threading.Lock()
_building = False


class GlobalBM25Index:
    """全量语料的 BM25 倒排索引

    属性:
        version: 对应 chunk store 版本号
        index: BM25Okapi 实例
        documents: 文档文本列表（与 index 顺序一致）
        doc_metas: 文档元数据列表（与 index 顺序一致）
        persist_dir: 持久化目录
    """

    def __init__(
        self,
        index: Any = None,
        documents: list[str] | None = None,
        doc_metas: list[dict[str, Any]] | None = None,
        version: str = "",
        persist_dir: str = DEFAULT_BM25_PERSIST_DIR,
    ):
        self.index = index  # BM25Okapi instance
        self.documents = documents or []
        self.doc_metas = doc_metas or []
        self.version = version
        self.persist_dir = persist_dir
        self._built = index is not None

    @property
    def is_built(self) -> bool:
        return self._built

    @property
    def total_docs(self) -> int:
        return len(self.documents)

    def retrieve(
        self,
        query_text: str,
        top_k: int = 10,
    ) -> list[Document]:
        """BM25 检索

        Args:
            query_text: 用户查询文本
            top_k: 返回结果数

        Returns:
            按 BM25 分数降序排列的 Document 列表
        """
        if not self._built or not self.index:
            logger.warning("BM25 索引未构建，无法检索")
            return []

        query_tokens = _tokenize(query_text)
        if not query_tokens:
            logger.warning("查询文本分词结果为空")
            return []

        raw_scores = self.index.get_scores(query_tokens)
        scores = raw_scores.tolist() if hasattr(raw_scores, "tolist") else list(raw_scores)

        # 按分数降序排列
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])

        results: list[Document] = []
        for doc_idx, score in ranked[:top_k]:
            if score <= 0:
                continue
            content = self.documents[doc_idx] if doc_idx < len(self.documents) else ""
            meta = dict(self.doc_metas[doc_idx]) if doc_idx < len(self.doc_metas) else {}
            doc = Document(
                id=meta.get("id", f"bm25_{doc_idx}"),
                content=content,
                meta=meta,
                score=float(score),
            )
            results.append(doc)

        logger.info(f'BM25 检索完成: query="{query_text[:50]}", {len(results)} 结果')
        return results

    def persist_path(self, version: str | None = None) -> Path:
        """获取持久化文件路径

        Args:
            version: 版本号，默认使用当前实例的 version
        """
        v = version or self.version
        return Path(self.persist_dir) / f"{v}.pkl"

    def save(self) -> str:
        """持久化索引到磁盘（原子写入）

        顺序：先写临时文件 → os.replace 原子替换 → 成功后才清理旧版本。
        避免写入中途失败导致当前版本文件损坏且旧版本已被删除。

        Returns:
            持久化文件路径
        """
        if not self._built:
            raise RuntimeError("无法持久化未构建的索引")

        persist_dir = Path(self.persist_dir)
        persist_dir.mkdir(parents=True, exist_ok=True)

        path = self.persist_path()
        data = {
            "version": self.version,
            "documents": self.documents,
            "doc_metas": self.doc_metas,
            "index": self.index,  # BM25Okapi 可 pickle
        }
        # 写入临时文件后原子替换，保证不会留下半截损坏文件
        tmp_path = path.with_suffix(".pkl.tmp")
        try:
            with open(tmp_path, "wb") as f:
                pickle.dump(data, f)
        except Exception:
            # 写入失败：清理临时文件，原文件保持不变
            tmp_path.unlink(missing_ok=True)
            raise
        os.replace(tmp_path, path)

        # 当前版本已安全落盘，再清理旧版本
        self._clean_old_persists()

        size_mb = path.stat().st_size / (1024 * 1024)
        logger.info(
            f"BM25 索引持久化完成: {path} "
            f"(版本={self.version[:8]}, "
            f"文档数={self.total_docs}, "
            f"大小={size_mb:.1f}MB)"
        )
        return str(path)

    def _clean_old_persists(self) -> None:
        """清理非当前版本的持久化文件"""
        persist_dir = Path(self.persist_dir)
        if not persist_dir.exists():
            return
        current_name = f"{self.version}.pkl"
        for f in persist_dir.iterdir():
            if f.is_file() and f.name.endswith(".pkl") and f.name != current_name:
                try:
                    f.unlink()
                    logger.debug(f"清理旧 BM25 索引: {f.name}")
                except OSError:
                    pass


def _tokenize(text: str) -> list[str]:
    """分词：中文按字符切分，英文/数字按单词

    与 hybrid_retriever.py 的 _tokenize 保持一致。
    """
    tokens = []
    for part in re.split(r"(\s+)", text):
        if not part.strip():
            continue
        # 中文部分：逐字符
        if any("\u4e00" <= c <= "\u9fff" for c in part):
            tokens.extend(list(part.strip()))
        else:
            # 英文/数字：按空格和标点
            tokens.extend(re.findall(r"[a-zA-Z0-9]+", part.lower()))
    return tokens


def _tokenize_document(text: str) -> list[str]:
    """文档分词（与 _tokenize 逻辑一致）"""
    return _tokenize(text)


def load_or_build(
    store_manager: Any,
    persist_dir: str = DEFAULT_BM25_PERSIST_DIR,
) -> GlobalBM25Index:
    """加载或构建 BM25 全量索引

    线程安全：使用 _build_lock 防止并发重建导致 pickle 损坏。
    双重检查锁定模式：锁外快速路径 + 锁内二次确认。

    1. 检查持久化目录是否存在对应版本号的索引文件
    2. 若存在且版本匹配 → 从磁盘加载（快速）
    3. 若不存在或版本不匹配 → 从 store_manager 全量 chunk 构建
    4. 构建完成后持久化到磁盘

    Args:
        store_manager: StoreManager 实例
        persist_dir: BM25 持久化目录

    Returns:
        GlobalBM25Index 实例
    """
    global _building
    t0 = time.time()

    # 获取当前 store 版本号
    version = store_manager.store_version
    if not version:
        logger.warning("store_version 为空，使用 'unknown' 作为 BM25 索引版本")
        version = "unknown"

    persist_path = Path(persist_dir) / f"{version}.pkl"

    # 快速路径：锁外检查，避免已持久化的索引仍需锁
    if persist_path.exists():
        try:
            with open(persist_path, "rb") as f:
                data = pickle.load(f)

            loaded_version = data.get("version", "")
            if loaded_version == version:
                index = GlobalBM25Index(
                    index=data["index"],
                    documents=data.get("documents", []),
                    doc_metas=data.get("doc_metas", []),
                    version=version,
                    persist_dir=persist_dir,
                )
                elapsed = (time.time() - t0) * 1000
                logger.info(
                    f"BM25 索引从磁盘加载完成: "
                    f"{index.total_docs} 文档, 耗时={elapsed:.0f}ms"
                )
                return index
            else:
                logger.info(
                    f"BM25 索引版本不匹配 "
                    f"(磁盘={loaded_version[:8]}, "
                    f"当前={version[:8]}), 重建中..."
                )
        except (pickle.UnpicklingError, EOFError, KeyError) as e:
            logger.warning(f"BM25 索引文件损坏，将重建: {e}")
        except Exception as e:
            logger.warning(f"BM25 索引加载异常，将重建: {e}")

    # 临界区：确保只有第一个线程执行重建
    with _build_lock:
        # 双重检查：持有锁后再次检查（可能另一个线程刚写入）
        if persist_path.exists() and not _building:
            try:
                with open(persist_path, "rb") as f:
                    data = pickle.load(f)
                loaded_version = data.get("version", "")
                if loaded_version == version:
                    index = GlobalBM25Index(
                        index=data["index"],
                        documents=data.get("documents", []),
                        doc_metas=data.get("doc_metas", []),
                        version=version,
                        persist_dir=persist_dir,
                    )
                    elapsed = (time.time() - t0) * 1000
                    logger.info(
                        f"BM25 索引从磁盘加载完成（锁后确认）: "
                        f"{index.total_docs} 文档, 耗时={elapsed:.0f}ms"
                    )
                    return index
            except Exception as e:
                logger.warning(f"BM25 索引锁后加载异常，将重建: {e}")

        if _building:
            logger.warning(
                "BM25 索引正在被其他线程构建，当前请求降级"
            )
            return GlobalBM25Index(version=version, persist_dir=persist_dir)

        # 标记构建中
        _building = True

    try:
        # 构建：从 store_manager 获取全量 chunk
        logger.info("BM25 全量索引构建中...")
        build_t0 = time.time()

        all_chunks = _get_all_chunks(store_manager)
        if not all_chunks:
            logger.warning("知识库为空，返回空 BM25 索引")
            return GlobalBM25Index(version=version, persist_dir=persist_dir)

        tokenized_docs = [_tokenize_document(d.content or "") for d in all_chunks]
        documents_text = [d.content or "" for d in all_chunks]
        doc_metas = [dict(d.meta or {}) for d in all_chunks]

        for i, d in enumerate(all_chunks):
            if d.id:
                doc_metas[i]["id"] = d.id
            doc_metas[i]["file_path"] = doc_metas[i].get("file_path", "")

        from rank_bm25 import BM25Okapi

        bm25 = BM25Okapi(tokenized_docs)

        build_elapsed = (time.time() - build_t0) * 1000
        logger.info(
            f"BM25 索引构建完成: {len(documents_text)} 文档, "
            f"耗时={build_elapsed:.0f}ms"
        )

        index = GlobalBM25Index(
            index=bm25,
            documents=documents_text,
            doc_metas=doc_metas,
            version=version,
            persist_dir=persist_dir,
        )

        try:
            index.save()
        except Exception as e:
            logger.error(
                f"BM25 索引持久化失败（不影响本次检索）: {e}"
            )

        total_elapsed = (time.time() - t0) * 1000
        logger.info(f"BM25 索引总耗时={total_elapsed:.0f}ms")

        return index
    finally:
        # 无论构建成功或失败，释放构建标志
        with _build_lock:
            _building = False


def _get_all_chunks(store_manager: Any) -> list[Document]:
    """从 store_manager 获取全量 chunk 文档

    使用 store_manager 的底层 chunk_store 的 filter_documents
    获取所有文档（无条件过滤）。
    """
    if not hasattr(store_manager, "chunk_store") or store_manager.chunk_store is None:
        logger.warning("store_manager 未初始化 chunk_store")
        return []

    try:
        # 空过滤器获取全量文档（Haystack filter_documents 支持 {} / None 表示全量）
        docs = store_manager.chunk_store.filter_documents(filters={})
        if docs is None:
            docs = []
        logger.debug(f"_get_all_chunks: 获取到 {len(docs)} 个文档")
        return docs  # type: ignore[no-any-return]
    except Exception as e:
        # 原备用分页路径调用同一 API 且未传 offset，会导致无限循环；
        # filter_documents 不支持分页参数，故直接返回空并记录错误。
        logger.error(f"获取全量 chunk 失败: {e}")
        return []
