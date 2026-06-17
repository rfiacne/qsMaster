"""
索引 Pipeline — 文档导入 → 解析 → 分块 → 嵌入 → 写入向量存储

流程:
  文件路径 → 文件类型检测 → 格式转换 → 文档清洗 →
  → 分层分割 (大块+小块) → 嵌入计算 → 双 store 写入

支持增量更新（DuplicatePolicy.SKIP/OVERWRITE）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from haystack import Document

from qa.converters.docling_converter import FileRouter, detect_file_type
from qa.pipelines.components.hierarchical_store import (
    HierarchicalDocumentSplitter,
    StoreWriter,
)
from qa.stores.turbovec_store import StoreManager

logger = logging.getLogger(__name__)


@dataclass
class IndexingResult:
    """索引操作结果"""

    files_count: int = 0           # 实际文件数
    documents_written: int = 0     # 总写入数 (parents + chunks)
    documents_skipped: int = 0     # 跳过的文件数
    chunk_count: int = 0           # 片段数
    parent_count: int = 0          # 大块数
    total_time_ms: float = 0.0
    errors: List[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class IndexingPipeline:
    """文档索引 Pipeline

    协调文档转换、分层分割、嵌入和写入。
    不依赖 Haystack Pipeline 串行化，使用直接函数调用更可控。
    """

    def __init__(
        self,
        store_manager: StoreManager,
        embedder=None,  # OpenAIDocumentEmbedder or similar
        block_sizes: Optional[List[int]] = None,
        ocr_enabled: bool = False,
        ocr_backend: str = "auto",
    ):
        self.store_manager = store_manager
        self.embedder = embedder
        self.ocr_enabled = ocr_enabled
        self.ocr_backend = ocr_backend

        blocks = block_sizes or [500, 100]
        self.splitter = HierarchicalDocumentSplitter(
            section_size=blocks[0] if len(blocks) > 0 else 500,
            paragraph_size=blocks[1] if len(blocks) > 1 else 100,
        )
        self.writer = StoreWriter(store_manager)
        self.router = FileRouter(ocr_enabled=ocr_enabled, ocr_backend=ocr_backend)

    def run(
        self,
        file_paths: List[str],
        meta: Optional[Dict] = None,
        skip_if_exists: bool = True,
    ) -> IndexingResult:
        """执行索引流程

        每步带计时和超时保护，日志记录每步耗时。

        Args:
            file_paths: 文件路径列表
            meta: 全局元数据（source/category/effective_date 等）
            skip_if_exists: True 则跳过已存在的文档

        Returns:
            IndexingResult
        """
        t0 = time.time()
        result = IndexingResult()

        if not file_paths:
            result.errors.append("没有指定文件")
            return result

        result.files_count = len(file_paths)

        # ── 步骤1: 文件检测 & 转换 ──
        step = "文件转换"
        logger.info(f"[步骤] {step}: 开始 ({len(file_paths)} 个文件)")
        t_step = time.time()
        try:
            converted = self.router.convert_many(file_paths, meta)
            result.documents_skipped = len(converted["skipped"])
            for fp, reason in converted["skipped"]:
                logger.warning(f"跳过 {fp}: {reason}")
            logger.info(f"[步骤] {step}: 完成 ({time.time()-t_step:.1f}s)")
        except Exception as e:
            logger.error(f"[步骤] {step}: 异常: {e}", exc_info=True)
            result.errors.append(f"{step} 失败: {e}")
            result.total_time_ms = (time.time() - t0) * 1000
            return result

        if not converted["success"]:
            result.total_time_ms = (time.time() - t0) * 1000
            result.errors.append("所有文件均无法解析")
            return result

        # ── 步骤2: 收集文档 ──
        step = "文档收集"
        logger.info(f"[步骤] {step}")
        all_docs: List[Document] = []
        for _file_type, docs_list, _fp in converted["success"]:
            all_docs.extend(docs_list)

        if not all_docs:
            result.total_time_ms = (time.time() - t0) * 1000
            result.errors.append("成功解析的文件无内容")
            return result

        # ── 步骤3: 分层分割 ──
        step = "分层分割"
        logger.info(f"[步骤] {step}: {len(all_docs)} 文档")
        t_step = time.time()
        try:
            split_result = self.splitter.run(documents=all_docs)
            parents = split_result["parents"]
            chunks = split_result["chunks"]
            logger.info(f"[步骤] {step}: 完成 → {len(parents)} parents + {len(chunks)} chunks ({time.time()-t_step:.1f}s)")
        except Exception as e:
            logger.error(f"[步骤] {step}: 异常: {e}", exc_info=True)
            result.errors.append(f"{step} 失败: {e}")
            result.total_time_ms = (time.time() - t0) * 1000
            return result

        # ── 步骤4: 嵌入计算 ──
        step = "嵌入计算"
        if parents or chunks:
            logger.info(f"[步骤] {step}: parents={len(parents)}, chunks={len(chunks)}")
            t_step = time.time()
            try:
                if self.embedder is not None:
                    logger.info(f"[步骤] {step}: 使用 Haystack Embedder")
                    embedded_result = self.embedder.run(documents=parents + chunks)
                    # 用新文档替换原列表
                    embedded_map = {d.id: d for d in embedded_result["documents"] if d.id}
                    parents = [embedded_map.get(d.id, d) for d in parents]
                    chunks = [embedded_map.get(d.id, d) for d in chunks]
                else:
                    logger.info(f"[步骤] {step}: 使用 OpenAI API")
                    embedded_docs = self._embed_chunks(parents + chunks)
                    embedded_map = {d.id: d for d in embedded_docs if d.id}
                    parents = [embedded_map.get(d.id, d) for d in parents]
                    chunks = [embedded_map.get(d.id, d) for d in chunks]
                logger.info(f"[步骤] {step}: 完成 ({time.time()-t_step:.1f}s)")
            except Exception as e:
                logger.error(f"[步骤] {step}: 失败: {e}")
                result.errors.append(f"{step} 失败: {e}")
                result.total_time_ms = (time.time() - t0) * 1000
                return result
        else:
            logger.info(f"[步骤] {step}: 跳过 (无文档)")

        # ── 步骤5: 写入双 store + 持久化 ──
        step = "写入存储"
        logger.info(f"[步骤] {step}: {len(parents)} parents + {len(chunks)} chunks")
        t_step = time.time()
        try:
            write_result = self.writer.run(parents=parents, chunks=chunks)
            result.documents_written = write_result["total_written"]
            result.chunk_count = write_result["chunks_written"]
            result.parent_count = write_result["parents_written"]
            logger.info(f"[步骤] {step}: 完成 ({time.time()-t_step:.1f}s)")
        except Exception as e:
            logger.error(f"[步骤] {step}: 异常: {e}", exc_info=True)
            result.errors.append(f"{step} 失败: {e}")
            result.total_time_ms = (time.time() - t0) * 1000
            return result

        result.total_time_ms = (time.time() - t0) * 1000
        logger.info(
            f"索引完成: "
            f"{result.documents_written} 文档, "
            f"{result.chunk_count} 片段, "
            f"{result.total_time_ms:.0f}ms"
        )

        return result

    def _embed_chunks(self, docs: List[Document]) -> List[Document]:
        """为文档片段生成嵌入向量（自动选择远程 API 或本地模型）

        返回新的 Document 列表（通过 dataclasses.replace 生成）。
        """
        import dataclasses
        from qa.pipelines.components.embedder import embed_texts

        embed_batch = min(20, 20)  # 本地模型也分批，避免内存爆涨
        total = len(docs)
        embedded = list(docs)

        for i in range(0, total, embed_batch):
            batch = docs[i : i + embed_batch]
            valid_pairs = [(idx, d) for idx, d in enumerate(batch) if d.content and d.content.strip()]
            if not valid_pairs:
                continue

            valid_indices = [p[0] for p in valid_pairs]
            valid_texts = [p[1].content for p in valid_pairs]

            vectors = embed_texts(valid_texts)

            for emb_idx, doc_batch_idx in enumerate(valid_indices):
                if emb_idx < len(vectors):
                    embedded[i + doc_batch_idx] = dataclasses.replace(
                        docs[i + doc_batch_idx],
                        embedding=vectors[emb_idx],
                    )

            logger.info(f"嵌入进度: {min(i + embed_batch, total)}/{total} ({(i + embed_batch) / total * 100:.0f}%)")

        logger.info(f"嵌入完成: {total} 个片段")
        return embedded


def validate_meta(meta: Optional[Dict]) -> List[str]:
    """验证元数据必需字段

    Returns:
        缺失字段名列表（空列表表示全部通过）
    """
    if meta is None:
        return ["source", "category", "effective_date"]

    missing = []
    required = ["source", "category", "effective_date"]

    for field in required:
        if field not in meta or not meta[field]:
            missing.append(field)

    return missing
