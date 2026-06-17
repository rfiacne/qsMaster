"""
交互式会话命令 — qa chat

提供 REPL 风格的交互式问答环境，支持多轮对话上下文。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Prompt

from qa.config.settings import get_settings
from qa.pipelines.querying import QueryPipeline
from qa.stores.turbovec_store import create_store_manager, IndexStatus

console = Console()
chat_app = typer.Typer(name="chat", help="交互式会话", no_args_is_help=True)


class ChatSession:
    """交互式会话管理"""

    def __init__(
        self,
        pipeline: QueryPipeline,
        max_history: int = 5,
    ):
        self.pipeline = pipeline
        self.max_history = max_history
        self.history: List[Dict] = []
        self.current_filters: Optional[Dict] = None

    def ask(self, question: str) -> None:
        """提问并显示回答"""
        result = self.pipeline.run(
            question=question,
            filters=self.current_filters,
        )

        if result.api_error == "EMPTY_INDEX":
            console.print("[yellow]知识库尚未建立，请先导入文档[/yellow]")
            return

        if result.api_error == "LLM_UNAVAILABLE":
            console.print("[red]服务暂时不可用（LLM API 不可用），请稍后重试[/red]")
            return

        if result.api_error == "EMBEDDING_UNAVAILABLE":
            console.print("[red]服务暂时不可用（Embedding API 不可用），请稍后重试[/red]")
            return

        if result.api_error == "API_TIMEOUT":
            console.print("[red]请求超时，请稍后重试[/red]")
            return

        # 显示回答
        if result.answer:
            console.print("\n[bold cyan]回答:[/bold cyan]")
            console.print(Markdown(result.answer))

        # 显示引用来源
        if result.sources:
            console.print("\n[bold yellow]引用来源:[/bold yellow]")
            for i, src in enumerate(result.sources[:3], 1):
                console.print(f"  [{i}] [green]{src.file_name}[/green] (score: {src.score:.3f})")

            if len(result.sources) > 3:
                console.print(f"  ... 还有 {len(result.sources) - 3} 个来源")

        # 耗时
        console.print(
            f"\n[dark_gray]检索: {result.retrieval_time_ms:.0f}ms"
            f" | 生成: {result.generation_time_ms:.0f}ms"
            f" | 总计: {result.total_time_ms:.0f}ms[/dark_gray]"
        )

        # 记录历史
        self.history.append({
            "question": question,
            "answer": result.answer,
            "sources": [s.document_id for s in result.sources],
        })

        # 限制历史长度
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def show_status(self) -> None:
        """显示知识库状态"""
        status = self.pipeline.store_manager.get_status()

        from rich.table import Table
        table = Table(title="知识库状态", title_style="bold cyan")
        table.add_column("指标", style="yellow")
        table.add_column("数值", style="white")

        table.add_row("文档数量", str(status.document_count))
        table.add_row("文档片段", str(status.chunk_count))
        table.add_row("索引大小", _format_bytes(status.index_size_bytes))
        table.add_row("最近更新", status.last_updated)

        console.print(table)

    def clear_history(self) -> None:
        """清除会话历史"""
        self.history = []
        console.print("[green]✓[/green] 会话历史已清除")

    def set_filter(self, filter_str: str) -> None:
        """设置元数据过滤"""
        try:
            self.current_filters = json.loads(filter_str)
            console.print(f"[green]✓[/green] 过滤器已设置")
        except json.JSONDecodeError as e:
            console.print(f"[red]过滤器 JSON 解析失败: {e}[/red]")


def _format_bytes(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


WELCOME_TEXT = """
[bold cyan]证券清算知识问答 — 交互式会话[/bold cyan]

输入问题开始问答，使用以下命令管理会话：

  [bold]/status[/bold]     查看知识库状态
  [bold]/filter JSON[/bold] 设置元数据过滤条件
  [bold]/clear[/bold]      清除会话上下文
  [bold]/history[/bold]    查看历史记录
  [bold]/quit[/bold]       退出

[dim]Press Ctrl+C to exit at any time[/dim]
"""


@chat_app.callback(invoke_without_command=True)
def chat(
    top_k: int = typer.Option(5, "--top-k", help="检索返回的最大文档数"),
    history: int = typer.Option(
        5, "--history", help="保留最近 N 轮对话上下文"
    ),
):
    """进入交互式会话模式"""
    settings = get_settings()

    # 初始化
    store = create_store_manager(
        bit_width=settings.vector_store.bit_width,
        similarity_function=settings.vector_store.similarity_function,
        persist_path=settings.vector_store.persist_path,
    )

    pipeline = QueryPipeline(
        store_manager=store,
        top_k=top_k or settings.retrieval.top_k,
        auto_merge_threshold=settings.retrieval.auto_merge_threshold,
    )

    session = ChatSession(pipeline=pipeline, max_history=history)

    console.print(WELCOME_TEXT)

    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]>[/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]再见！[/yellow]")
            break

        if not user_input:
            continue

        # 命令处理
        if user_input.startswith("/"):
            cmd = user_input.lower().strip()

            if cmd == "/quit" or cmd == "/exit":
                console.print("[yellow]再见！[/yellow]")
                break

            elif cmd == "/status":
                session.show_status()

            elif cmd.startswith("/filter "):
                filter_str = user_input[len("/filter "):].strip()
                session.set_filter(filter_str)

            elif cmd == "/clear":
                session.clear_history()

            elif cmd == "/history":
                if session.history:
                    console.print("\n[bold]会话历史[/bold]")
                    for i, h in enumerate(session.history, 1):
                        console.print(f"  [{i}] Q: {h['question']}")
                else:
                    console.print("[dim]暂无历史记录[/dim]")

            elif cmd == "/help":
                console.print(WELCOME_TEXT)

            else:
                console.print(f"[red]未知命令: {cmd}[/red]")
                console.print("输入 /help 查看可用命令")

            continue

        # 问答
        session.ask(user_input)
