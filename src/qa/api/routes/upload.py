"""文档上传索引路由 — /upload"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from qa.api.dependencies import (
    get_index_pipeline,
    get_store,
    reset_runtime_singletons,
)
from qa.api.middleware import check_rate_limit
from qa.config.settings import get_settings
from qa.pipelines.indexing import validate_meta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/qa")


@router.post("/upload")
async def upload(
    files: list[UploadFile],
    source: str = Form(default=""),
    category: str = Form(default=""),
    effective_date: str = Form(default=""),
    meta: str = Form(default=""),  # 兼容前端 JSON 字符串格式
    _auth=Depends(check_rate_limit),
):
    """上传并索引文档"""
    # 如果前端传了 meta JSON 字符串，从中解析字段
    if meta:
        try:
            meta_dict = json.loads(meta)
            source = source or meta_dict.get("source", "")
            category = category or meta_dict.get("category", "")
            effective_date = effective_date or meta_dict.get("effective_date", "")
        except json.JSONDecodeError:
            pass

    meta_dict = {"source": source, "category": category, "effective_date": effective_date}

    missing = validate_meta(meta_dict)
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少必需元数据: {', '.join(missing)}")

    # 保存上传文件到临时目录
    tmp_dir = Path(tempfile.mkdtemp(prefix="qa_upload_"))
    file_paths = []
    skipped_files: list[dict] = []  # 记录跳过的重复文件

    timed_out = False
    try:
        store = get_store()
        for f in files:
            dest = tmp_dir / Path(f.filename).name
            content = await f.read()
            dest.write_bytes(content)

            # 计算 MD5 并检查是否已存在
            file_md5 = hashlib.md5(content).hexdigest()
            if await asyncio.to_thread(store.has_file_md5, file_md5):
                skipped_files.append({"filename": f.filename, "md5": file_md5})
                logger.info(f"跳过重复文件: {f.filename} (MD5: {file_md5})")
                continue

            file_paths.append(str(dest))

        if not file_paths:
            return {
                "files_count": 0,
                "documents_written": 0,
                "documents_skipped": len(skipped_files),
                "segments": 0,
                "parents": 0,
                "errors": [],
                "time_ms": 0,
                "skipped_files": skipped_files,
            }

        pipeline = get_index_pipeline()
        # 在线程池中运行同步阻塞的 pipeline，避免卡死 event loop
        srv_settings = get_settings()
        # ponytail: 文件数 × 单文档超时 + 嵌入 + 180s 缓冲（PaddleOCR 模型加载/分块/写入）
        pipe_timeout = max(
            180,
            len(file_paths) * srv_settings.indexing.doc_timeout_seconds
            + srv_settings.embedding.timeout_seconds
            + 180,
        )
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(pipeline.run, file_paths, meta_dict),
                timeout=pipe_timeout,
            )
        except TimeoutError:
            timed_out = True
            raise RuntimeError(
                f"索引超时 ({pipe_timeout}s)。后台线程仍在运行，临时文件将在完成后自动清理。"
            )

        # 显式持久化（StoreWriter 不再自动 save）
        await asyncio.to_thread(store.save)

        # 索引已变更：清空 pipeline/BM25 单例，查询缓存按版本自动失效
        reset_runtime_singletons()

        return {
            "files_count": result.files_count,
            "documents_written": result.documents_written,
            "documents_skipped": result.documents_skipped + len(skipped_files),
            "segments": result.chunk_count,  # 检索用片段数
            "parents": result.parent_count,  # 上下文用大块数
            "errors": result.errors,
            "time_ms": result.total_time_ms,
            "skipped_files": skipped_files,  # 重复跳过的文件列表
        }
    finally:
        # 清理临时文件 — 超时不删，后台线程仍在使用
        import shutil

        if not timed_out:
            shutil.rmtree(tmp_dir, ignore_errors=True)
