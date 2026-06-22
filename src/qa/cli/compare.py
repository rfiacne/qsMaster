"""
混合检索对比测试命令 — qa compare

对同一查询同时执行三种检索模式并对比结果：
1. 纯向量检索（语义匹配）
2. 纯 BM25 检索（关键词精确匹配）
3. 混合检索（RRF 融合）

用法:
    qa compare "CCASS 交收指令截止时间" [OPTIONS]
    qa compare "沪深交易所清算流程" --top-k 10
"""

from __future__ import annotations

import json
import time
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from qa.config.settings import get_settings
from qa.pipelines.components.embedder import embed_query
from qa.pipelines.components.hybrid_retriever import HybridRetriever
from qa.stores.turbovec_store import create_store_manager

console = Console()
compare_app = typer.Typer(
    name="compare",
    help="混合检索对比测试",
    no_args_is_help=True,
)


@compare_app.callback(invoke_without_command=True)
def compare(
    query: str = typer.Argument(..., help="测试查询文本"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="每种模式的检索结果数"),
    format: str = typer.Option("text", "--format", "-f", help="输出格式: text | json"),
    show_all: bool = typer.Option(False, "--all", "-a", help="显示所有候选结果（不截断内容）"),
):
    """对比三种检索模式的结果差异"""
    settings = get_settings()
    # 保存并临时禁用 Early Exit（避免污染进程内缓存单例）
    _ee_enabled = settings.early_exit.enabled
    settings.early_exit.enabled = False

    try:
        _compare_inner(query, top_k, format, show_all, settings)
    finally:
        settings.early_exit.enabled = _ee_enabled


def _compare_inner(
    query: str,
    top_k: int,
    format: str,
    show_all: bool,
    settings,
) -> None:
    """内部执行比较（包装在 try/finally 中以恢复 settings）"""
    # 初始化向量存储
    store = create_store_manager(
        bit_width=settings.vector_store.bit_width,
        similarity_function=settings.vector_store.similarity_function,
        persist_path=settings.vector_store.persist_path,
    )

    # 检查知识库是否为空
    if store.count_chunks() == 0:
        console.print("[red]知识库为空，请先导入文档[/red]")
        raise typer.Exit(1)

    # 构建 BM25 全量索引（与 server/CLI 共享同一构建逻辑）
    from qa.pipelines.factory import build_bm25_index

    bm25_index = build_bm25_index(settings, store)

    # 嵌入查询
    try:
        query_embedding = embed_query(query)
    except Exception as e:
        console.print(f"[red]嵌入查询失败: {e}[/red]")
        raise typer.Exit(2)

    # ── 1) 纯向量检索 ──
    t0 = time.time()
    vec_results = store.retrieve(
        query_embedding=query_embedding,
        top_k=top_k,
    )
    vec_time = (time.time() - t0) * 1000

    # ── 2) 混合检索（RRF 融合） ──
    hybrid = HybridRetriever(
        store_manager=store,
        vector_weight=settings.retrieval.hybrid_vector_weight,
        top_k=top_k,
        bm25_index=bm25_index,
    )
    t0 = time.time()
    hybrid_results = hybrid.retrieve(
        query_embedding=query_embedding,
        query_text=query,
        top_k=top_k,
    )
    hybrid_time = (time.time() - t0) * 1000

    # ── 3) 纯 BM25 检索 ──
    bm25_hybrid = HybridRetriever(
        store_manager=store,
        vector_weight=0.0,  # 纯 BM25
        top_k=top_k,
        bm25_index=bm25_index,
    )
    t0 = time.time()
    bm25_results = bm25_hybrid.retrieve(
        query_embedding=query_embedding,
        query_text=query,
        top_k=top_k,
    )
    bm25_time = (time.time() - t0) * 1000

    # ── 输出 ──
    if format == "json":
        data = {
            "query": query,
            "top_k": top_k,
            "vector": {
                "count": len(vec_results),
                "time_ms": round(vec_time, 1),
                "results": _results_to_dict(vec_results),
            },
            "bm25": {
                "count": len(bm25_results),
                "time_ms": round(bm25_time, 1),
                "results": _results_to_dict(bm25_results),
            },
            "hybrid": {
                "count": len(hybrid_results),
                "time_ms": round(hybrid_time, 1),
                "results": _results_to_dict(hybrid_results),
            },
        }
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    # text 格式
    _print_summary(query, top_k, vec_time, bm25_time, hybrid_time)

    # 交并集分析
    vec_ids = {d.id for d in vec_results if d.id}
    bm25_ids = {d.id for d in bm25_results if d.id}
    hybrid_ids = {d.id for d in hybrid_results if d.id}

    common_vh = vec_ids & hybrid_ids
    common_bh = bm25_ids & hybrid_ids
    common_all = vec_ids & bm25_ids & hybrid_ids

    console.print("\n[bold]交并集分析:[/bold]")
    console.print(
        f"  向量结果中出现在混合结果中: [green]{len(common_vh)}/{len(vec_results)}[/green]"
    )
    console.print(
        f"  BM25结果中出现在混合结果中: [green]{len(common_bh)}/{len(bm25_results)}[/green]"
    )
    console.print(f"  三种模式共同命中: [cyan]{len(common_all)}[/cyan]")

    # 纯向量独有、纯BM25独有
    vec_only = vec_ids - bm25_ids
    bm25_only = bm25_ids - vec_ids
    if vec_only:
        console.print(f"  纯向量独有: [yellow]{len(vec_only)}[/yellow] 条")
    if bm25_only:
        console.print(f"  BM25独有: [yellow]{len(bm25_only)}[/yellow] 条")

    console.print()  # 空行

    # 逐个模式显示结果
    _print_mode_results("1️⃣  纯向量检索", vec_results, show_all, "vector")
    _print_mode_results("2️⃣  纯 BM25 检索", bm25_results, show_all, "bm25")
    _print_mode_results("3️⃣  混合检索 (RRF)", hybrid_results, show_all, "hybrid")

    # 耗时对比
    table = Table(title="⏱ 耗时对比", title_style="bold")
    table.add_column("模式", style="cyan")
    table.add_column("耗时", justify="right")
    table.add_column("结果数", justify="right")

    table.add_row("纯向量", f"{vec_time:.0f}ms", str(len(vec_results)))
    table.add_row("纯 BM25", f"{bm25_time:.0f}ms", str(len(bm25_results)))
    table.add_row("混合", f"{hybrid_time:.0f}ms", str(len(hybrid_results)))

    console.print()
    console.print(table)


def _print_summary(query: str, top_k: int, vt: float, bt: float, ht: float) -> None:
    """打印查询摘要"""
    console.print(f"\n[bold cyan]查询:[/bold cyan] {query}")
    console.print(f"[bold cyan]Top-K:[/bold cyan] {top_k}")
    console.print()


def _print_mode_results(
    title: str,
    results: list,
    show_all: bool,
    source_type: str,
) -> None:
    """打印单个检索模式的详细结果"""
    if not results:
        console.print(f"\n[bold]{title}[/bold] [yellow](无结果)[/yellow]")
        return

    table = Table(title=title, title_style="bold")
    table.add_column("#", style="dim", width=3)
    table.add_column("文档", width=40)
    table.add_column("分数", justify="right", width=8)
    table.add_column("来源", width=8)

    for i, doc in enumerate(results, 1):
        meta = doc.meta or {}
        file_name = _short_name(meta.get("file_path", "unknown"))
        score = meta.get("vec_score", doc.score or 0.0)
        st = meta.get("source_type", source_type)

        # 截断文件名过长
        if len(file_name) > 38:
            file_name = "..." + file_name[-35:]

        type_tag = ""
        if st == "vector":
            type_tag = "[向量]"
        elif st == "bm25":
            type_tag = "[全文]"
        elif st == "hybrid":
            type_tag = "[双源]"

        table.add_row(
            str(i),
            file_name,
            f"{score:.4f}",
            type_tag,
        )

    console.print()
    console.print(table)

    # 如果 show_all，展示前 3 条的内容片段
    if show_all:
        for i, doc in enumerate(results[:3], 1):
            content = (doc.content or "")[:200]
            console.print(f"  [dim]#{i} 内容:[/dim] {content}...")


def _results_to_dict(results: list) -> list[dict[str, Any]]:
    """将检索结果转为字典列表"""
    data = []
    for doc in results:
        meta = doc.meta or {}
        data.append(
            {
                "id": doc.id,
                "file_name": _short_name(meta.get("file_path", "unknown")),
                "score": round(doc.score or 0.0, 4),
                "vec_score": round(meta.get("vec_score", 0.0), 4),
                "hybrid_score": round(meta.get("hybrid_score", 0.0), 4),
                "source_type": meta.get("source_type", ""),
                "content_preview": (doc.content or "")[:200],
            }
        )
    return data


from qa.utils import short_name as _short_name
