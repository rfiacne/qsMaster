"""
交互式会话命令 — qa chat

提供 REPL 风格的交互式问答环境，支持持久化多轮对话。

用法:
    qa chat                    创建新会话
    qa chat --session <id>     恢复已有会话
    qa chat --list             列出最近会话
    qa chat --resume           恢复最近未关闭的会话
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Prompt
from rich.table import Table

from qa.config.settings import get_settings
from qa.pipelines.components.session_store import SessionStore
from qa.pipelines.factory import build_query_pipeline, build_store

console = Console()
chat_app = typer.Typer(name="chat", help="交互式会话", no_args_is_help=True)


class ChatSession:
    """持久化交互式会话管理"""

    def __init__(
        self,
        pipeline: QueryPipeline,
        session_store: SessionStore,
        max_history: int = 5,
        session_id: str | None = None,
    ):
        self.pipeline = pipeline
        self.session_store = session_store
        self.max_history = max_history
        self.current_filters: dict | None = None

        # 恢复或新建会话
        if session_id:
            self.session = session_store.get(session_id)
            if not self.session:
                console.print(f"[yellow]会话 {session_id[:8]}... 未找到，创建新会话[/yellow]")
        if not hasattr(self, "session") or self.session is None:
            self.session = session_store.create(max_turns=max_history + 5)

    @property
    def session_id(self) -> str:
        return self.session.id

    def ask(self, question: str) -> None:
        """提问并显示回答"""
        # 注入对话历史
        history_text = self.session.get_context_text(max_turns=self.max_history)

        result = self.pipeline.run(
            question=question,
            filters=self.current_filters,
            conversation_history=history_text,
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
        if result.from_standard_answer:
            match_label = "精确匹配" if result.match_type == "exact" else "模糊匹配"
            console.print(f"\n[bold cyan]回答 [标准答案库 | {match_label}]:[/bold cyan]")
            console.print(Markdown(result.answer))
        elif result.answer:
            console.print("\n[bold cyan]回答:[/bold cyan]")
            console.print(Markdown(result.answer))

        # Faithfulness 校验标识
        if result.faithfulness:
            f_result = result.faithfulness.get("result", "skipped")
            f_summary = result.faithfulness.get("summary", "")
            if f_result == "pass":
                console.print("[green]✓ 忠实度校验通过[/green]")
            elif f_result == "partial":
                console.print(f"[yellow]⚠ 部分声明无检索支撑 — {f_summary}[/yellow]")
            elif f_result == "fail":
                console.print(f"[red]✗ 忠实度校验不通过，回答已降级 — {f_summary}[/red]")

        # 显示引用来源
        if result.sources:
            console.print("\n[bold yellow]引用来源:[/bold yellow]")
            for i, src in enumerate(result.sources[:3], 1):
                type_tag = ""
                if src.source_type == "vector":
                    type_tag = " [向量]"
                elif src.source_type == "bm25":
                    type_tag = " [全文]"
                elif src.source_type == "hybrid":
                    type_tag = " [双源]"
                console.print(
                    f"  [{i}] [green]{src.file_name}[/green]{type_tag} (score: {src.score:.3f})"
                )

            if len(result.sources) > 3:
                console.print(f"  ... 还有 {len(result.sources) - 3} 个来源")

        # 耗时
        if result.from_standard_answer:
            gen_text = "跳过(标准答案)"
        else:
            gen_text = f"{result.generation_time_ms:.0f}ms"
        console.print(
            f"\n[dark_gray]检索: {result.retrieval_time_ms:.0f}ms"
            f" | 生成: {gen_text}"
            f" | 总计: {result.total_time_ms:.0f}ms[/dark_gray]"
        )

        # 持久化记录
        sources_for_store = [
            {
                "file_name": s.file_name,
                "score": s.score,
                "source_type": s.source_type,
            }
            for s in result.sources
        ]
        self.session.add_turn(question, result.answer or "", sources_for_store)
        self.session_store.save(self.session)

    def show_status(self) -> None:
        """显示知识库状态"""
        status = self.pipeline.store_manager.get_status()
        table = Table(title="知识库状态", title_style="bold cyan")
        table.add_column("指标", style="yellow")
        table.add_column("数值", style="white")
        table.add_row("文档数量", str(status.document_count))
        table.add_row("文档片段", str(status.chunk_count))
        table.add_row("索引大小", format_bytes(status.index_size_bytes))
        table.add_row("最近更新", status.last_updated)
        console.print(table)

    def clear_history(self) -> None:
        """清除会话历史"""
        self.session.turns.clear()
        self.session_store.save(self.session)
        console.print("[green]✓[/green] 会话历史已清除")

    def set_filter(self, filter_str: str) -> None:
        """设置元数据过滤"""
        try:
            self.current_filters = json.loads(filter_str)
            console.print("[green]✓[/green] 过滤器已设置")
        except json.JSONDecodeError as e:
            console.print(f"[red]过滤器 JSON 解析失败: {e}[/red]")


from qa.utils import format_bytes


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
    history: int = typer.Option(5, "--history", help="保留最近 N 轮对话上下文"),
    session_id: str | None = typer.Option(None, "--session", "-s", help="恢复已有会话 ID"),
    list_sessions: bool = typer.Option(False, "--list", "-l", help="列出最近会话"),
    resume: bool = typer.Option(False, "--resume", "-r", help="恢复最近未关闭的会话"),
):
    """进入交互式会话模式

    支持持久化多轮对话，会话历史自动保存，可随时恢复。
    """
    settings = get_settings()

    # 列出会话模式
    if list_sessions:
        session_store = SessionStore()
        session_store.load()
        sessions = session_store.list_recent(limit=20)
        if not sessions:
            console.print("[yellow]暂无历史会话[/yellow]")
            raise typer.Exit(0)
        table = Table(title="最近会话")
        table.add_column("ID", style="cyan", width=14)
        table.add_column("标题", width=40)
        table.add_column("轮次", justify="right")
        table.add_column("最后更新")
        for s in sessions:
            table.add_row(
                s.id[:12],
                s.title[:38] if s.title else "(新会话)",
                str(len(s.turns)),
                s.updated_at or "-",
            )
        console.print(table)
        raise typer.Exit(0)

    # 恢复最近会话
    if resume and not session_id:
        session_store = SessionStore()
        session_store.load()
        recent = session_store.list_recent(limit=1)
        if recent:
            session_id = recent[0].id
            console.print(f"[dim]恢复最近会话: {session_id[:8]}...[/dim]")

    # 全链路组件由工厂统一构建
    pipeline = build_query_pipeline(settings, top_k=top_k)

    # 持久化会话
    session_store = SessionStore()
    session_store.load()

    if session_id:
        console.print(f"[dim]恢复会话: {session_id[:8]}...[/dim]")

    session = ChatSession(
        pipeline=pipeline,
        session_store=session_store,
        max_history=history,
        session_id=session_id,
    )

    console.print(WELCOME_TEXT)
    console.print(f"[dim]会话 ID: {session.session_id[:8]}... | 输入 /quit 退出[/dim]")

    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]>[/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]再见！[/yellow]")
            session_store.save(session.session)
            break

        if not user_input:
            continue

        # 命令处理
        if user_input.startswith("/"):
            cmd = user_input.lower().strip()

            if cmd == "/quit" or cmd == "/exit":
                session_store.save(session.session)
                console.print("[yellow]再见！[/yellow]")
                break

            elif cmd == "/status":
                session.show_status()

            elif cmd.startswith("/filter "):
                filter_str = user_input[len("/filter ") :].strip()
                session.set_filter(filter_str)

            elif cmd == "/clear":
                session.clear_history()

            elif cmd == "/history":
                turns = session.session.turns
                if turns:
                    console.print("\n[bold]会话历史[/bold]")
                    for i, turn in enumerate(turns, 1):
                        console.print(f"  [{i}] Q: {turn.question[:60]}")
                else:
                    console.print("[dim]暂无历史记录[/dim]")

            elif cmd.startswith("/session"):
                console.print(f"当前会话: [cyan]{session.session_id}[/cyan]")
                console.print(f"标题: {session.session.title or '(无)'}")
                console.print(f"轮次: {len(session.session.turns)}")

            elif cmd == "/help":
                console.print(WELCOME_TEXT)

            else:
                console.print(f"[red]未知命令: {cmd}[/red]")
                console.print("输入 /help 查看可用命令")

            continue

        # 问答
        session.ask(user_input)
