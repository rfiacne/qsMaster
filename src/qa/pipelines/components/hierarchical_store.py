"""
分层文档存储组件 — HierarchicalDocumentSplitter + StoreWriter

将文档按层级切分为大块（section 级，~500 字）和小块（paragraph 级，~100 字），
分别写入 parent_store 和 chunk_store。

与 Haystack 的 AutoMergingRetriever 配合实现"小块检索、大块喂 LLM"策略。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from haystack import Document, component

logger = logging.getLogger(__name__)


@component
class HierarchicalDocumentSplitter:
    """分层文档分割器

    将文档按递归字符切分为两层：
    - Level 1 (section): ~500 字大块，存入 parent_store
    - Level 2 (paragraph): ~100 字小块，存入 chunk_store（做嵌入和检索）

    小块保留 parent_id 引用大块，检索时 AutoMergingRetriever 自动合并。
    """

    def __init__(
        self,
        section_size: int = 500,
        paragraph_size: int = 100,
        overlap: int = 20,
        split_max_iterations: int = 100000,
    ):
        """
        Args:
            section_size: 大块目标字符数
            paragraph_size: 小块目标字符数
            overlap: 分块重叠字符数
            split_max_iterations: 文本分割最大迭代次数
        """
        self.section_size = section_size
        self.paragraph_size = paragraph_size
        self.overlap = overlap
        self.split_max_iterations = split_max_iterations

    @component.output_types(
        parents=list[Document],
        chunks=list[Document],
    )
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        """分割文档为 parent 和 chunk 两层

        Returns:
            {
                "parents": [Document, ...],   # Level 1 大块
                "chunks": [Document, ...],     # Level 2 小块
            }
        """
        parents: list[Document] = []
        chunks: list[Document] = []

        for doc in documents:
            text = doc.content or ""
            if not text.strip():
                continue

            source_id = doc.id or str(uuid.uuid4())
            base_meta = dict(doc.meta) if doc.meta else {}

            # --- Level 1: section 级大块 ---
            section_chunks = self._split_text(text, self.section_size)

            for sec_idx, sec_text in enumerate(section_chunks):
                parent_id = f"{source_id}_sec_{sec_idx}"
                parent_doc = Document(
                    id=parent_id,
                    content=sec_text,
                    meta={
                        **base_meta,
                        "source_id": source_id,
                        "level": 1,
                        "section_idx": sec_idx,
                        "parent_id": None,  # 顶层
                    },
                )
                parents.append(parent_doc)

                # --- Level 2: paragraph 级小块 ---
                para_chunks = self._split_text(sec_text, self.paragraph_size)
                for para_idx, para_text in enumerate(para_chunks):
                    chunk_id = f"{parent_id}_para_{para_idx}"
                    chunk_doc = Document(
                        id=chunk_id,
                        content=para_text,
                        meta={
                            **base_meta,
                            "source_id": source_id,
                            "level": 2,
                            "section_idx": sec_idx,
                            "para_idx": para_idx,
                            "parent_id": parent_id,
                        },
                    )
                    chunks.append(chunk_doc)

        # 为 parents 添加 children_ids
        parent_children: dict[str, list[str]] = {}
        for chunk in chunks:
            pid = chunk.meta.get("parent_id") if chunk.meta else None
            if pid:
                parent_children.setdefault(pid, []).append(chunk.id or "")

        for parent in parents:
            if parent.id and parent.id in parent_children:
                if parent.meta is not None:
                    parent.meta["children_ids"] = parent_children[parent.id]

        logger.info(
            f"分层分割完成: {len(documents)} 文档 → "
            f"{len(parents)} 大块 + {len(chunks)} 小块"
        )

        return {"parents": parents, "chunks": chunks}

    def _split_text(self, text: str, chunk_size: int) -> list[str]:
        """按目标字符数分割文本（保留段落边界）"""
        if len(text) <= chunk_size:
            return [text]

        chunks: list[str] = []
        start = 0
        max_iterations = self.split_max_iterations
        iteration = 0

        while start < len(text) and iteration < max_iterations:
            iteration += 1
            if iteration >= max_iterations:
                logger.warning(f"_split_text 达到最大迭代次数 ({max_iterations})，强制终止")
                remaining = text[start:].strip()
                if remaining:
                    chunks.append(remaining)
                break

            # 在 chunk_size 范围内找最近的自然断点
            end = min(start + chunk_size, len(text))

            if end < len(text):
                # 从后往前找段落断点
                newline_pos = text.rfind("\n\n", start, end)
                if newline_pos > start and newline_pos > end - chunk_size // 2:
                    end = newline_pos + 1
                else:
                    # 找句号断点
                    sentence_pos = text.rfind("。", start, end)
                    if sentence_pos > start:
                        end = sentence_pos + 1
                    elif text[end - 1] not in (" ", "\n"):
                        # 找空格断点
                        space_pos = text.rfind(" ", start, end)
                        if space_pos > start:
                            end = space_pos

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            # 重叠部分
            start = end - (self.overlap if end < len(text) else 0)

        return chunks


@component
class StoreWriter:
    """写入器 — 将分层文档写入双 store

    将 parents 写入 parent_store，chunks 写入 chunk_store，
    并自动持久化。
    """

    def __init__(self, store_manager):
        self.store_manager = store_manager

    @component.output_types(
        parents_written=int,
        chunks_written=int,
        total_written=int,
    )
    def run(
        self,
        parents: list[Document],
        chunks: list[Document],
    ) -> dict[str, Any]:
        """写入分层的文档到向量存储"""
        from haystack.document_stores.types import DuplicatePolicy

        parent_count = self.store_manager.write_parents(
            parents, policy=DuplicatePolicy.SKIP
        )
        chunk_count = self.store_manager.write_chunks(
            chunks, policy=DuplicatePolicy.SKIP
        )

        return {
            "parents_written": parent_count,
            "chunks_written": chunk_count,
            "total_written": parent_count + chunk_count,
        }
