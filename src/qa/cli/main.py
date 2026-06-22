"""
CLI 主入口 — qa 命令

用法:
    qa ask "问题"
    qa chat
    qa index <path>
    qa status
    qa remove --source CSDC
    qa config show
"""

from __future__ import annotations

import typer
from rich.console import Console

from qa import __version__

from .answer import answer_app
from .ask import ask_app
from .chat import chat_app
from .compare import compare_app
from .config import config_app
from .index import index
from .review import review_app

console = Console()

# 主应用
app = typer.Typer(
    name="qa",
    help="证券清算与技术知识问答系统",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(config_app, name="config")
app.add_typer(ask_app, name="ask")
app.add_typer(chat_app, name="chat")
app.add_typer(answer_app, name="answer")
app.add_typer(compare_app, name="compare")
app.add_typer(review_app, name="review")
app.command(name="index")(index)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", help="显示版本号"),
):
    """证券清算与技术知识问答系统（Securities QA Agent）"""
    if version:
        console.print(f"Securities QA Agent v{__version__}")
        raise typer.Exit(0)

    if ctx.invoked_subcommand is None:
        console.print("[yellow]使用 qa --help 查看可用命令[/yellow]")


@app.command()
def status(
    format: str = typer.Option(
        "text", "--format", help="输出格式: text | json"
    ),
):
    """查看知识库状态"""
    from qa.config.settings import get_settings
    from qa.stores.turbovec_store import create_store_manager

    settings = get_settings()
    store = create_store_manager(
        bit_width=settings.vector_store.bit_width,
        similarity_function=settings.vector_store.similarity_function,
        persist_path=settings.vector_store.persist_path,
    )

    index_status = store.get_status()

    if format == "json":
        import json
        console.print(json.dumps({
            "document_count": index_status.document_count,
            "chunk_count": index_status.chunk_count,
            "parent_count": index_status.parent_count,
            "index_size_bytes": index_status.index_size_bytes,
            "last_updated": index_status.last_updated,
            "bit_width": index_status.bit_width,
            "dim": index_status.dim,
            "persist_path": index_status.persist_path,
        }, ensure_ascii=False, indent=2))
    else:
        from rich.table import Table

        table = Table(title="知识库状态", title_style="bold cyan")
        table.add_column("指标", style="yellow")
        table.add_column("数值", style="white")

        table.add_row("文档数量", str(index_status.document_count))
        table.add_row("文档片段", str(index_status.chunk_count))
        table.add_row("大块数量", str(index_status.parent_count))
        table.add_row("索引大小", format_bytes(index_status.index_size_bytes))
        table.add_row("最近更新", index_status.last_updated)
        table.add_row("量化宽度", f"{index_status.bit_width}-bit")
        table.add_row("向量维度", str(index_status.dim or "自动"))
        table.add_row("持久化路径", index_status.persist_path)

        console.print(table)


@app.command()
def remove(
    source: str = typer.Option(None, "--source", help="按来源机构删除"),
    category: str = typer.Option(None, "--category", help="按类别删除"),
    ids: str = typer.Option(None, "--ids", help="按文档 ID 列表删除（逗号分隔）"),
    all: bool = typer.Option(False, "--all", help="清空整个索引"),
):
    """删除文档"""
    from qa.config.settings import get_settings
    from qa.stores.turbovec_store import create_store_manager

    settings = get_settings()
    store = create_store_manager(
        bit_width=settings.vector_store.bit_width,
        similarity_function=settings.vector_store.similarity_function,
        persist_path=settings.vector_store.persist_path,
    )

    if all:
        count = store.delete_all()
        console.print(f"[green]✓[/green] 已清空索引（删除 {count} 条记录）")
        return

    if source:
        filters = {
            "operator": "AND",
            "conditions": [
                {"field": "meta.source", "operator": "==", "value": source}
            ],
        }
        count = store.delete_by_filter(filters)
        console.print(f"[green]✓[/green] 已删除来源 '{source}' 的 {count} 条记录")
    elif category:
        filters = {
            "operator": "AND",
            "conditions": [
                {"field": "meta.category", "operator": "==", "value": category}
            ],
        }
        count = store.delete_by_filter(filters)
        console.print(f"[green]✓[/green] 已删除类别 '{category}' 的 {count} 条记录")
    elif ids:
        id_list = [i.strip() for i in ids.split(",") if i.strip()]
        count = store.delete_documents(id_list)
        console.print(f"[green]✓[/green] 已删除 {count} 条记录")
    else:
        console.print("[red]请指定删除条件: --source, --category, --ids 或 --all[/red]")
        raise typer.Exit(1)


from qa.utils import format_bytes

if __name__ == "__main__":
    app()
