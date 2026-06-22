"""
索引管理命令 — qa index

支持单文件、目录批量、增量/全量重建索引。
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from qa.config.settings import get_settings
from qa.pipelines.indexing import IndexingPipeline, validate_meta
from qa.stores.turbovec_store import create_store_manager

logger = logging.getLogger(__name__)
console = Console()


def _collect_files(paths: list[str]) -> list[str]:
    """收集所有要索引的文件路径

    目录会递归扫描支持的文档格式。
    """
    supported = {
        ".pdf", ".docx", ".doc",
        ".xlsx", ".xls",
        ".md", ".markdown",
        ".html", ".htm",
        ".txt",
        ".jpg", ".jpeg", ".png",
    }
    files: list[str] = []

    for path_str in paths:
        p = Path(path_str)
        if not p.exists():
            console.print(f"[red]路径不存在: {path_str}[/red]")
            continue

        if p.is_file():
            if p.suffix.lower() in supported:
                files.append(str(p))
            else:
                console.print(f"[yellow]跳过不支持的文件: {p.name}[/yellow]")
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in supported:
                    files.append(str(f))

    return files


def index(
    path: str = typer.Argument(..., help="文件或目录路径"),
    source: str | None = typer.Option(None, "--source", help="来源机构（必需）"),
    category: str | None = typer.Option(None, "--category", help="文档类别（必需）"),
    effective_date: str | None = typer.Option(
        None, "--effective-date", help="生效日期 YYYY-MM-DD（必需）"
    ),
    version: str | None = typer.Option(None, "--version", help="版本号（可选）"),
    tags: str | None = typer.Option(None, "--tags", help="标签列表（逗号分隔，可选）"),
    description: str | None = typer.Option(None, "--description", help="文档摘要（可选）"),
    rebuild: bool = typer.Option(False, "--rebuild", help="全量重建索引"),
    bit_width: int | None = typer.Option(
        None, "--bit-width", help="turbovec 量化宽度: 4 或 2"
    ),
    ocr_backend: str = typer.Option(
        "auto", "--ocr-backend",
        help="PDF 解析后端: auto | docling | opendataloader | paddle | none"
    ),
):
    """构建或增量更新索引"""
    settings = get_settings()

    # 收集文件
    files = _collect_files([path])
    if not files:
        console.print(
            "[red]未找到支持的文档文件（支持: PDF/DOCX/DOC/XLSX/XLS/MD/HTML/TXT/JPG/PNG）[/red]"
        )
        raise typer.Exit(4)
    console.print(f"找到 [bold]{len(files)}[/bold] 个文档文件")

    # 验证元数据
    meta = {}
    if source:
        meta["source"] = source
    if category:
        meta["category"] = category
    if effective_date:
        meta["effective_date"] = effective_date
    if version:
        meta["version"] = version
    if tags:
        meta["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if description:
        meta["description"] = description

    missing = validate_meta(meta)
    if missing:
        console.print(
            f"[red]缺少必需元数据: {', '.join(missing)}[/red]\n"
            f"请提供 --source, --category 和 --effective-date"
        )
        raise typer.Exit(1)

    # 如果需要全量重建，先清空
    bw = bit_width or settings.vector_store.bit_width
    if bw not in (2, 4):
        console.print("[red]bit_width 必须为 2 或 4[/red]")
        raise typer.Exit(4)

    store = create_store_manager(
        bit_width=bw,
        similarity_function=settings.vector_store.similarity_function,
        persist_path=settings.vector_store.persist_path,
    )

    if rebuild:
        console.print("[yellow]全量重建模式: 清空现有索引...[/yellow]")
        store.delete_all()

    # 初始化索引 Pipeline
    pipeline = IndexingPipeline(
        store_manager=store,
        block_sizes=settings.retrieval.block_sizes,
        ocr_enabled=settings.indexing.ocr_enabled or ocr_backend != "none",
        ocr_backend=ocr_backend,
        doc_timeout_sec=settings.indexing.doc_timeout_seconds,
        embed_timeout_sec=float(settings.embedding.timeout_seconds),
    )

    # 带进度条执行索引
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            f"索引 {len(files)} 个文档...", total=len(files)
        )

        # 按 batch_size 分批处理
        batch_size = settings.indexing.batch_size
        total_written = 0
        total_skipped = 0
        all_errors = []

        for i in range(0, len(files), batch_size):
            batch = files[i : i + batch_size]
            result = pipeline.run(
                file_paths=batch,
                meta=meta,
            )
            total_written += result.documents_written
            total_skipped += result.documents_skipped
            all_errors.extend(result.errors)
            progress.update(task, advance=len(batch))

    # 显式持久化（StoreWriter 不再自动 save）
    store.save()

    # 输出结果
    console.print(
        f"\n[green]✓[/green] 索引完成: "
        f"[bold]{len(files)}[/bold] 个文件 → [bold]{total_written}[/bold] 个片段"
        f"{', [yellow]' + str(total_skipped) + ' 跳过[/yellow]' if total_skipped else ''}"
    )

    # 显示索引状态
    status = store.get_status()
    console.print(
        f"  文档: {status.document_count} | "
        f"片段: {status.chunk_count} | "
        f"大小: {_format_bytes(status.index_size_bytes)}"
    )

    if all_errors:
        for err in all_errors:
            console.print(f"[red]⚠ {err}[/red]")


from qa.utils import format_bytes as _format_bytes
