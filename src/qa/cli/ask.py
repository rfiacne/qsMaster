"""
单次问答命令 — qa ask
"""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from qa.config.settings import get_settings
from qa.pipelines.querying import QueryPipeline
from qa.stores.turbovec_store import create_store_manager

console = Console()
ask_app = typer.Typer(name="ask", help="单次问答", no_args_is_help=True)


@ask_app.callback(invoke_without_command=True)
def ask(
    question: str = typer.Argument(..., help="用户问题"),
    top_k: int = typer.Option(5, "--top-k", help="检索返回的最大文档数"),
    filter_json: Optional[str] = typer.Option(
        None, "--filter", help="元数据过滤 JSON"
    ),
    no_answer: bool = typer.Option(False, "--no-answer", help="仅返回检索结果，不调用 LLM"),
    format: str = typer.Option("text", "--format", help="输出格式: text | json"),
):
    """单次问答"""
    settings = get_settings()

    # 初始化向量存储
    store = create_store_manager(
        bit_width=settings.vector_store.bit_width,
        similarity_function=settings.vector_store.similarity_function,
        persist_path=settings.vector_store.persist_path,
    )

    # 初始化 QueryPipeline
    pipeline = QueryPipeline(
        store_manager=store,
        top_k=top_k or settings.retrieval.top_k,
        auto_merge_threshold=settings.retrieval.auto_merge_threshold,
    )

    # 解析过滤器
    filters = None
    if filter_json:
        try:
            filters = json.loads(filter_json)
        except json.JSONDecodeError as e:
            console.print(f"[red]过滤器 JSON 解析失败: {e}[/red]")
            raise typer.Exit(4)

    # 执行问答
    result = pipeline.run(
        question=question,
        top_k=top_k,
        filters=filters,
        no_llm=no_answer,
    )

    # 输出
    if format == "json":
        console.print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_text_result(result)


def _print_text_result(result):
    """以文本格式输出问答结果"""
    if result.api_error == "EMPTY_INDEX":
        console.print("[yellow]知识库尚未建立，请先导入文档[/yellow]")
        raise typer.Exit(1)

    if result.api_error == "LLM_UNAVAILABLE":
        console.print("[red]服务暂时不可用（LLM API 不可用），请稍后重试[/red]")
        raise typer.Exit(2)

    if result.api_error == "EMBEDDING_UNAVAILABLE":
        console.print("[red]服务暂时不可用（Embedding API 不可用），请稍后重试[/red]")
        raise typer.Exit(2)

    if result.api_error == "API_TIMEOUT":
        console.print("[red]请求超时，请稍后重试[/red]")
        raise typer.Exit(3)

    if result.answer:
        console.print("\n[bold cyan]回答:[/bold cyan]")
        console.print(Markdown(result.answer))
    else:
        console.print("\n[yellow]未生成回答（仅检索模式或无相关文档）[/yellow]")

    # 引用来源
    if result.sources:
        console.print("\n[bold yellow]引用来源:[/bold yellow]")
        for i, src in enumerate(result.sources, 1):
            can_print = src.content[:150] + "..." if len(src.content) > 150 else src.content
            console.print(f"\n  [{i}] [green]{src.file_name}[/green] (score: {src.score:.3f})")
            console.print(f"      {can_print}")

    # 耗时
    console.print(
        f"\n[dark_gray]检索: {result.retrieval_time_ms:.0f}ms"
        f" | 生成: {result.generation_time_ms:.0f}ms"
        f" | 总计: {result.total_time_ms:.0f}ms[/dark_gray]"
    )
