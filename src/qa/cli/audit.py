"""
审计日志查询命令 — qa audit

查看问答审计记录（JSONL 持久化，仅追加，不可删除）。

用法:
    qa audit list [OPTIONS]
    qa audit show <id>
    qa audit stats
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from qa.pipelines.components.audit_logger import AuditStore

console = Console()
audit_app = typer.Typer(name="audit", help="审计日志查询", no_args_is_help=True)


def _get_store() -> AuditStore:
    return AuditStore()


@audit_app.command(name="list")
def list_records(
    start: str | None = typer.Option(None, "--start", "-s", help="起始时间 (>=)，如 2024-01-01"),
    end: str | None = typer.Option(None, "--end", "-e", help="结束时间 (<=)，如 2024-06-30"),
    keyword: str | None = typer.Option(None, "--keyword", "-k", help="关键词搜索（问题/回答）"),
    session: str | None = typer.Option(None, "--session", help="按会话 ID 筛选"),
    page: int = typer.Option(1, "--page", "-p", help="页码"),
    page_size: int = typer.Option(20, "--page-size", "-n", help="每页条数"),
    format: str = typer.Option("text", "--format", "-f", help="输出格式: text | json"),
):
    """查询审计日志"""
    store = _get_store()
    records, total = store.query(
        start=start,
        end=end,
        keyword=keyword,
        session_id=session,
        page=page,
        page_size=page_size,
    )

    if not records:
        console.print("[yellow]无匹配的审计记录[/yellow]")
        raise typer.Exit(0)

    if format == "json":
        data = [
            {
                "idx": (page - 1) * page_size + i + 1,
                "timestamp": r.timestamp,
                "question": r.question[:80],
                "answer_preview": (r.answer or "")[:100],
                "total_time_ms": r.total_time_ms,
                "faithfulness": r.faithfulness_result,
                "from_standard": r.from_standard_answer,
            }
            for i, r in enumerate(records)
        ]
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    table = Table(title=f"审计日志 (第 {page} 页, 共 {total} 条)")
    table.add_column("#", style="dim", width=4)
    table.add_column("时间", width=18)
    table.add_column("问题", width=40)
    table.add_column("耗时", justify="right", width=8)
    table.add_column("Eval", width=8)
    table.add_column("来源", width=8)

    for i, r in enumerate(records, (page - 1) * page_size + 1):
        q = r.question[:38] + ".." if len(r.question) > 38 else r.question
        eval_tag = {
            "pass": "[green]通过[/green]",
            "fail": "[red]不通过[/red]",
            "partial": "[yellow]部分[/yellow]",
        }.get(r.faithfulness_result, "-")

        source = "标准" if r.from_standard_answer else "检索"
        table.add_row(
            str(i),
            r.timestamp,
            q,
            f"{r.total_time_ms:.0f}ms",
            eval_tag,
            source,
        )

    console.print(table)
    if total > page * page_size:
        console.print(f"[dim]输入 --page {page + 1} 查看下一页[/dim]")


@audit_app.command()
def show(
    record_id: str = typer.Argument(..., help="记录 ID（audit list 显示的第一列）"),
):
    """查看单条审计记录详情（按记录 ID 定位，与 list 过滤条件解耦）"""
    store = _get_store()
    records_all, _ = store.query(page=1, page_size=10000)
    # 按 ID 匹配（也支持按序号兼容：纯数字且 ≤ 记录数时尝试旧序号方式）
    matched = None
    if record_id.isdigit():
        idx = int(record_id)
        if 1 <= idx <= len(records_all):
            matched = records_all[idx - 1]
    if matched is None:
        for r in records_all:
            if r.id == record_id:
                matched = r
                break
    if matched is None:
        console.print(f"[red]未找到记录: {record_id}[/red]")
        raise typer.Exit(1)

    r = matched
    console.print(f"[bold cyan]时间:[/bold cyan] {r.timestamp}")
    console.print(f"[bold cyan]问题:[/bold cyan] {r.question}")
    console.print(f"[bold cyan]回答:[/bold cyan] {r.answer or '(无)'}")
    src_label = "标准答案库" if r.from_standard_answer else "检索生成"
    console.print(f"[bold cyan]来源:[/bold cyan] {src_label}")
    if r.match_type:
        console.print(f"[bold cyan]匹配类型:[/bold cyan] {r.match_type}")
    console.print(f"[bold cyan]检索耗时:[/bold cyan] {r.retrieval_time_ms:.0f}ms")
    console.print(f"[bold cyan]生成耗时:[/bold cyan] {r.generation_time_ms:.0f}ms")
    console.print(f"[bold cyan]总耗时:[/bold cyan] {r.total_time_ms:.0f}ms")
    f_score = f"{r.faithfulness_result} (score: {r.faithfulness_score:.3f})"
    console.print(f"[bold cyan]Faithfulness:[/bold cyan] {f_score}")
    if r.api_error:
        console.print(f"[bold red]错误:[/bold red] {r.api_error}")

    if r.sources:
        console.print(f"\n[bold yellow]引用来源 ({len(r.sources)}):[/bold yellow]")
        for i, s in enumerate(r.sources[:10], 1):
            file_name = s.get("file_name", "?")
            score = s.get("score", 0)
            st = s.get("source_type", "")
            type_tag = {"vector": " [向量]", "bm25": " [全文]", "hybrid": " [双源]"}.get(st, "")
            console.print(f"  [{i}] {file_name}{type_tag} (score: {score:.3f})")


@audit_app.command()
def stats():
    """审计日志统计"""
    store = _get_store()
    s = store.get_stats()
    table = Table(title="审计日志统计", title_style="bold cyan")
    table.add_column("指标", style="yellow")
    table.add_column("数值", style="white")
    table.add_row("总记录数", str(s["total"]))
    table.add_row("最早记录", s["earliest"] or "-")
    table.add_row("最近记录", s["latest"] or "-")
    table.add_row("平均耗时", f"{s['avg_time_ms']:.0f}ms" if s["avg_time_ms"] else "-")
    console.print(table)
