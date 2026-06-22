"""
审核队列管理命令 — qa review

查看、标注、统计审核队列项。

用法:
    qa review list [OPTIONS]
    qa review show <id>
    qa review approve <id> [OPTIONS]
    qa review partial <id> [OPTIONS]
    qa review reject <id> [OPTIONS]
    qa review stats
    qa review archive [--max-days 90]
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from qa.config.settings import get_settings
from qa.pipelines.components.review_queue import ReviewWorkflow

console = Console()
review_app = typer.Typer(
    name="review",
    help="审核队列管理",
    no_args_is_help=True,
)


def _get_workflow() -> ReviewWorkflow:
    """获取 ReviewWorkflow 实例"""
    settings = get_settings()
    store_path = getattr(settings.early_exit, "store_path", "./data/standard_answers")
    review_path = store_path + "_review"
    workflow = ReviewWorkflow(store_path=review_path)
    workflow.ensure_loaded()
    return workflow


@review_app.command(name="list")
def list_items(
    status: str | None = typer.Option(
        None, "--status", "-s", help="按状态筛选 (pending/correct/partial/incorrect/archived)"
    ),
    limit: int = typer.Option(20, "--limit", "-l", help="显示条数"),
    format: str = typer.Option("text", "--format", "-f", help="输出格式: text | json"),
):
    """列出审核队列"""
    workflow = _get_workflow()
    items = workflow.store.list_all(status=status)

    if not items:
        console.print("[yellow]审核队列为空[/yellow]")
        raise typer.Exit(0)

    if format == "json":
        data = [i.to_dict() for i in items[:limit]]
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    table = Table(title=f"审核队列 ({len(items)} 项)")
    table.add_column("#", style="dim", width=4)
    table.add_column("ID", style="cyan", width=14)
    table.add_column("问题", width=36)
    table.add_column("Eval", justify="right", width=6)
    table.add_column("优先级", width=6)
    table.add_column("状态", width=10)
    table.add_column("标注", width=10)
    table.add_column("审核人", width=10)

    for i, item in enumerate(items[:limit], 1):
        q = item.question[:34] + ".." if len(item.question) > 34 else item.question
        priority_tag = "[red]高[/red]" if item.priority == "high" else "普通"
        status_tag = {
            "pending": "[yellow]待审核[/yellow]",
            "reviewed": "[green]已审核[/green]",
            "archived": "[dim]已归档[/dim]",
        }.get(item.status, item.status)

        label_tag = {
            "correct": "[green]正确[/green]",
            "partial": "[yellow]部分[/yellow]",
            "incorrect": "[red]错误[/red]",
        }.get(item.label, "")

        table.add_row(
            str(i),
            item.id[:12] + "..",
            q,
            f"{item.faithfulness_score:.2f}",
            priority_tag,
            status_tag,
            label_tag,
            item.reviewer or "-",
        )

    console.print(table)
    console.print(f"\n总计: [bold]{len(items)}[/bold] 项")
    if limit < len(items):
        console.print(f"[dim]显示前 {limit} 项，共 {len(items)} 项[/dim]")


@review_app.command()
def show(
    item_id: str = typer.Argument(..., help="审核项 ID"),
    format: str = typer.Option("text", "--format", "-f", help="输出格式: text | json"),
):
    """查看审核项详情"""
    workflow = _get_workflow()
    item = workflow.store.get(item_id)
    if not item:
        console.print(f"[red]未找到审核项: {item_id}[/red]")
        raise typer.Exit(1)

    if format == "json":
        console.print(json.dumps(item.to_dict(), ensure_ascii=False, indent=2))
        return

    console.print(f"[bold cyan]ID:[/bold cyan] {item.id}")
    console.print(f"[bold cyan]问题:[/bold cyan] {item.question}")
    console.print(f"[bold cyan]回答:[/bold cyan] {item.answer[:300]}")
    f_val = f"{item.faithfulness_score:.4f} ({item.faithfulness_result})"
    console.print(f"[bold cyan]Faithfulness:[/bold cyan] {f_val}")
    console.print(f"[bold cyan]优先级:[/bold cyan] {item.priority}")
    console.print(f"[bold cyan]状态:[/bold cyan] {item.status}")
    console.print(f"[bold cyan]标注:[/bold cyan] {item.label or '-'}")
    console.print(f"[bold cyan]审核人:[/bold cyan] {item.reviewer or '-'}")
    console.print(f"[bold cyan]修正意见:[/bold cyan] {item.review_comment or '-'}")
    console.print(f"[bold cyan]入队时间:[/bold cyan] {item.created_at or '-'}")
    console.print(f"[bold cyan]审核时间:[/bold cyan] {item.reviewed_at or '-'}")

    if item.sources:
        console.print(f"\n[bold yellow]引用来源 ({len(item.sources)}):[/bold yellow]")
        for i, src in enumerate(item.sources[:5], 1):
            name = src.get("file_name", src.get("source", "unknown"))
            console.print(f"  [{i}] {name}")


@review_app.command()
def approve(
    item_id: str = typer.Argument(..., help="审核项 ID"),
    reviewer: str = typer.Option("", "--reviewer", "-r", help="审核人"),
    comment: str = typer.Option("", "--comment", "-c", help="修正意见"),
    no_convert: bool = typer.Option(False, "--no-convert", help="不自动进行语义匹配入库"),
):
    """标注为正确（自动进行语义匹配入库）"""
    workflow = _get_workflow()
    std_store = None
    if not no_convert:
        from qa.config.settings import get_settings
        from qa.pipelines.components.early_exit import StandardAnswerStore
        settings = get_settings()
        std_store = StandardAnswerStore(store_path=settings.early_exit.store_path)
    if workflow.label(
        item_id, "correct",
        reviewer=reviewer, comment=comment,
        standard_answer_store=std_store,
    ):
        console.print("[green]✓[/green] 已标注为正确")
        if not no_convert:
            console.print("  [dim]已自动执行语义匹配入库[/dim]")
    else:
        console.print("[red]标注失败（可能已审核或不存在）[/red]")
        raise typer.Exit(1)


@review_app.command()
def partial(
    item_id: str = typer.Argument(..., help="审核项 ID"),
    reviewer: str = typer.Option("", "--reviewer", "-r", help="审核人"),
    comment: str = typer.Option("", "--comment", "-c", help="修正意见"),
):
    """标注为部分正确"""
    workflow = _get_workflow()
    if workflow.label(item_id, "partial", reviewer=reviewer, comment=comment):
        console.print("[yellow]✓[/yellow] 已标注为部分正确")
    else:
        console.print("[red]标注失败（可能已审核或不存在）[/red]")
        raise typer.Exit(1)


@review_app.command()
def reject(
    item_id: str = typer.Argument(..., help="审核项 ID"),
    reviewer: str = typer.Option("", "--reviewer", "-r", help="审核人"),
    comment: str = typer.Option("", "--comment", "-c", help="修正意见"),
):
    """标注为错误"""
    workflow = _get_workflow()
    if workflow.label(item_id, "incorrect", reviewer=reviewer, comment=comment):
        console.print("[red]✓[/red] 已标注为错误")
    else:
        console.print("[red]标注失败（可能已审核或不存在）[/red]")
        raise typer.Exit(1)


@review_app.command()
def stats(
    format: str = typer.Option("text", "--format", "-f", help="输出格式: text | json"),
):
    """查看审核统计"""
    workflow = _get_workflow()
    s = workflow.get_stats()

    if format == "json":
        console.print(json.dumps(s.to_dict(), ensure_ascii=False, indent=2))
        return

    table = Table(title="审核统计", title_style="bold cyan")
    table.add_column("指标", style="yellow")
    table.add_column("数值", style="white")

    table.add_row("总计", str(s.total))
    table.add_row("待审核", str(s.pending))
    table.add_row("已审核(正确)", str(s.correct))
    table.add_row("已审核(部分)", str(s.partial))
    table.add_row("已审核(错误)", str(s.incorrect))
    table.add_row("已归档", str(s.archived))
    table.add_row("完成率", f"{s.completion_rate * 100:.1f}%")

    console.print(table)


@review_app.command()
def archive(
    max_days: int = typer.Option(90, "--max-days", "-d", help="保留天数"),
):
    """归档超过保留期限的待审核项"""
    workflow = _get_workflow()
    count = workflow.archive_old(max_days=max_days)
    console.print(f"[green]✓[/green] 已归档 {count} 项")
