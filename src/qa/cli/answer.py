"""
标准答案库管理命令 — qa answer

支持标准问答对的增删改查、批量导入导出和种子数据加载。

用法:
    qa answer add "问题" "答案" [OPTIONS]
    qa answer list [OPTIONS]
    qa answer remove <id>
    qa answer import <file.json>
    qa answer export [--output file.json]
    qa answer seed [--force]
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from qa.pipelines.components.early_exit import (
    EarlyExitMatcher,
    StandardAnswer,
    StandardAnswerStore,
)

console = Console()
answer_app = typer.Typer(
    name="answer",
    help="标准答案库管理",
    no_args_is_help=True,
)


def _get_store() -> StandardAnswerStore:
    """获取标准答案库存储实例"""
    from qa.config.settings import get_settings
    settings = get_settings()
    store = StandardAnswerStore(store_path=settings.early_exit.store_path)
    store.load()
    return store


def _get_matcher() -> EarlyExitMatcher:
    """获取 EarlyExitMatcher 实例（重建索引用）"""
    from qa.config.settings import get_settings
    settings = get_settings()
    return EarlyExitMatcher(
        store_path=settings.early_exit.store_path,
        fuzzy_threshold=settings.early_exit.fuzzy_threshold,
        enabled=settings.early_exit.enabled,
    )


@answer_app.command()
def add(
    question: str = typer.Argument(..., help="标准问题"),
    answer: str = typer.Argument(..., help="标准答案"),
    category: str = typer.Option("", "--category", "-c", help="类别标签"),
    tags: str = typer.Option("", "--tags", "-t", help="逗号分隔的标签列表"),
    source: str = typer.Option("manual", "--source", "-s", help="录入来源"),
    match_strategy: str = typer.Option(
        "both", "--match", "-m",
        help="匹配策略: exact | fuzzy | both",
    ),
    force: bool = typer.Option(
        False, "--force", "-f",
        help="覆盖已有（相同 ID 的问题）",
    ),
):
    """添加一条标准问答对"""
    store = _get_store()

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    item = StandardAnswer(
        question=question.strip(),
        answer=answer.strip(),
        category=category,
        tags=tag_list,
        source=source,
        match_strategy=match_strategy,
    )

    # 检查是否已存在相同问题
    existing = store.get_by_question(question)
    if existing and not force:
        console.print(
            f"[yellow]标准答案已存在 (id: {existing.id[:12]}...)[/yellow]"
        )
        console.print(f"  问题: {existing.question[:50]}")
        console.print(f"  答案: {existing.answer[:80]}...")
        console.print("  使用 --force 覆盖")
        raise typer.Exit(1)

    is_new = store.add(item)

    # 重建模糊匹配索引
    if match_strategy in ("fuzzy", "both"):
        matcher = _get_matcher()
        matcher.store = store
        matcher.rebuild_index()

    action = "新增" if is_new else "覆盖"
    console.print(f"[green]✓[/green] {action}标准答案成功 (id: {item.id[:12]}...)")


@answer_app.command(name="list")
def list_answers(
    category: str | None = typer.Option(
        None, "--category", "-c", help="按类别筛选"
    ),
    keyword: str | None = typer.Option(
        None, "--keyword", "-k", help="关键词搜索"
    ),
    status: str | None = typer.Option(
        None, "--status", "-s", help="按状态筛选 (enabled/disabled/candidate)"
    ),
    format: str = typer.Option(
        "text", "--format", "-f", help="输出格式: text | json"
    ),
):
    """列出标准答案库"""
    store = _get_store()

    if status:
        answers = store.list_by_status(status)
    elif keyword:
        answers = store.search(keyword)
    elif category:
        answers = store.list_by_category(category)
    else:
        answers = store.list_all()

    if not answers:
        console.print("[yellow]标准答案库为空[/yellow]")
        raise typer.Exit(0)

    if format == "json":
        data = [a.to_dict() for a in answers]
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    # text 格式
    table = Table(title=f"标准答案库 ({len(answers)} 条)")
    table.add_column("#", style="dim", width=4)
    table.add_column("ID", style="cyan", width=14)
    table.add_column("问题", width=36)
    table.add_column("类别", width=10)
    table.add_column("状态", width=8)
    table.add_column("别名", justify="right", width=6)
    table.add_column("来源", width=10)

    for i, a in enumerate(answers, 1):
        q = a.question[:34] + ".." if len(a.question) > 34 else a.question
        status_tag = {
            "enabled": "[green]启用[/green]",
            "disabled": "[dim]禁用[/dim]",
            "candidate": "[yellow]候选[/yellow]",
        }.get(a.status, a.status or "enabled")
        table.add_row(
            str(i),
            a.id[:12],
            q,
            a.category or "-",
            status_tag,
            str(len(a.aliases)),
            a.source or "-",
        )

    console.print(table)
    console.print(f"\n总计: [bold]{len(answers)}[/bold] 条标准答案")


@answer_app.command()
def remove(
    answer_id: str = typer.Argument(..., help="标准答案 ID"),
):
    """删除一条标准答案"""
    store = _get_store()
    if store.remove(answer_id):
        console.print(f"[green]✓[/green] 已删除标准答案 {answer_id[:12]}...")
        # 重建索引
        matcher = _get_matcher()
        matcher.store = store
        matcher.rebuild_index()
    else:
        console.print(f"[red]未找到标准答案: {answer_id[:12]}...[/red]")
        raise typer.Exit(1)


@answer_app.command(name="import")
def import_(
    path: str = typer.Argument(..., help="JSON 文件路径"),
    source: str = typer.Option(
        "import", "--source", "-s", help="导入来源标记"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="覆盖已有条目"
    ),
):
    """从 JSON 文件批量导入标准答案"""
    file_path = Path(path)
    if not file_path.exists():
        console.print(f"[red]文件不存在: {path}[/red]")
        raise typer.Exit(1)

    try:
        with open(file_path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        console.print(f"[red]文件解析失败: {e}[/red]")
        raise typer.Exit(2)

    if not isinstance(raw, list):
        console.print("[red]JSON 格式错误：需要一个列表[/red]")
        raise typer.Exit(2)

    # 如果不是 force，跳过已存在的
    store = _get_store()
    if not force:
        # 过滤掉已存在的
        filtered = []
        skipped = 0
        for item in raw:
            q = item.get("question", "")
            if q and store.get_by_question(q):
                skipped += 1
            else:
                filtered.append(item)
        raw = filtered
        if skipped > 0:
            console.print(f"[yellow]跳过 {skipped} 条已存在的答案（使用 --force 覆盖）[/yellow]")

    added, overwritten, errors = store.import_batch(raw)

    # 重建索引
    if added + overwritten > 0:
        matcher = _get_matcher()
        matcher.store = store
        matcher.rebuild_index()

    console.print(
        f"[green]✓[/green] 导入完成: "
        f"新增 {added}, 覆盖 {overwritten}, 错误 {len(errors)}"
    )
    for err in errors:
        console.print(f"  [red]⚠ {err}[/red]")


@answer_app.command()
def export(
    output: str | None = typer.Option(
        None, "--output", "-o", help="输出文件路径（默认 stdout）"
    ),
    category: str | None = typer.Option(
        None, "--category", "-c", help="按类别导出"
    ),
):
    """导出标准答案库到 JSON"""
    store = _get_store()

    if category:
        answers = store.list_by_category(category)
    else:
        answers = store.list_all()

    data = [a.to_dict() for a in answers]
    json_str = json.dumps(data, ensure_ascii=False, indent=2)

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json_str)
        console.print(
            f"[green]✓[/green] 已导出 {len(data)} 条标准答案 → {output}"
        )
    else:
        console.print(json_str)


@answer_app.command()
def seed(
    force: bool = typer.Option(
        False, "--force", "-f", help="覆盖已有种子数据"
    ),
):
    """加载内置种子数据（62 条证券清算高频问答）

    仅在对应问题不存在时加载，除非使用 --force。
    """
    seed_path = Path(__file__).parent.parent / "data" / "seed_answers.json"
    if not seed_path.exists():
        console.print(f"[red]种子数据文件不存在: {seed_path}[/red]")
        raise typer.Exit(1)

    try:
        with open(seed_path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        console.print(f"[red]种子数据解析失败: {e}[/red]")
        raise typer.Exit(2)

    store = _get_store()

    if not force:
        filtered = []
        skipped = 0
        for item in raw:
            q = item.get("question", "")
            if q and store.get_by_question(q):
                skipped += 1
            else:
                filtered.append(item)
        raw = filtered
        if skipped > 0:
            console.print(
                f"[yellow]跳过 {skipped} 条已存在的种子数据（使用 --force 覆盖）[/yellow]"
            )

    added, overwritten, errors = store.import_batch(raw)

    if added + overwritten > 0:
        matcher = _get_matcher()
        matcher.store = store
        matcher.rebuild_index()

    console.print(
        f"[green]✓[/green] 种子数据加载完成: "
        f"新增 {added}, 覆盖 {overwritten}, 错误 {len(errors)}"
    )
    for err in errors:
        console.print(f"  [red]⚠ {err}[/red]")


@answer_app.command()
def disable(
    answer_id: str = typer.Argument(..., help="标准答案 ID"),
):
    """禁用标准答案（不再参与 Early Exit 匹配）"""
    store = _get_store()
    if store.set_status(answer_id, "disabled"):
        # 重建索引
        matcher = _get_matcher()
        matcher.store = store
        matcher.rebuild_index()
        console.print(f"[yellow]✓[/yellow] 已禁用标准答案 {answer_id[:12]}...")
    else:
        console.print(f"[red]未找到标准答案: {answer_id}[/red]")
        raise typer.Exit(1)


@answer_app.command()
def enable(
    answer_id: str = typer.Argument(..., help="标准答案 ID"),
):
    """启用标准答案"""
    store = _get_store()
    if store.set_status(answer_id, "enabled"):
        matcher = _get_matcher()
        matcher.store = store
        matcher.rebuild_index()
        console.print(f"[green]✓[/green] 已启用标准答案 {answer_id[:12]}...")
    else:
        console.print(f"[red]未找到标准答案: {answer_id}[/red]")
        raise typer.Exit(1)


@answer_app.command()
def alias(
    answer_id: str = typer.Argument(..., help="标准答案 ID"),
    alias_question: str = typer.Argument(..., help="别名问题文本"),
    score: float = typer.Option(1.0, "--score", "-s", help="语义相似度分数 (0~1)"),
):
    """为标准答案添加别名问题"""
    store = _get_store()
    if store.add_alias(answer_id, alias_question, similarity_score=score):
        matcher = _get_matcher()
        matcher.store = store
        matcher.rebuild_index()
        console.print(f"[green]✓[/green] 已为 {answer_id[:12]}... 添加别名")
    else:
        console.print(f"[red]未找到标准答案: {answer_id}[/red]")
        raise typer.Exit(1)


@answer_app.command(name="aliases")
def list_aliases(
    answer_id: str = typer.Argument(..., help="标准答案 ID"),
):
    """列出标准答案的所有别名问题"""
    store = _get_store()
    a = store.get(answer_id)
    if not a:
        console.print(f"[red]未找到标准答案: {answer_id}[/red]")
        raise typer.Exit(1)
    if not a.aliases:
        console.print("[yellow]暂无别名问题[/yellow]")
        return
    from rich.table import Table
    table = Table(title=f"别名问题 ({len(a.aliases)} 个)")
    table.add_column("#", style="dim")
    table.add_column("问题", width=60)
    table.add_column("相似度", justify="right")
    for i, al in enumerate(a.aliases, 1):
        table.add_row(str(i), al.question, f"{al.similarity_score:.3f}")
    console.print(table)


@answer_app.command()
def edit(
    answer_id: str = typer.Argument(..., help="标准答案 ID"),
    question: str | None = typer.Option(None, "--question", "-q", help="新问题文本"),
    answer: str | None = typer.Option(None, "--answer", "-a", help="新答案文本"),
    category: str | None = typer.Option(None, "--category", "-c", help="新类别"),
    match_strategy: str | None = typer.Option(None, "--match", "-m", help="匹配策略"),
):
    """编辑标准答案"""
    store = _get_store()
    a = store.get(answer_id)
    if not a:
        console.print(f"[red]未找到标准答案: {answer_id}[/red]")
        raise typer.Exit(1)
    changed = False
    if question:
        a.question = question.strip()
        changed = True
    if answer:
        a.answer = answer.strip()
        changed = True
    if category:
        a.category = category.strip()
        changed = True
    if match_strategy:
        if match_strategy not in ("exact", "fuzzy", "both"):
            console.print("[red]match_strategy 必须为 exact/fuzzy/both[/red]")
            raise typer.Exit(1)
        a.match_strategy = match_strategy
        changed = True
    if changed:
        a.updated_at = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
        store.save()
        matcher = _get_matcher()
        matcher.store = store
        matcher.rebuild_index()
        console.print(f"[green]✓[/green] 已更新标准答案 {answer_id[:12]}...")
    else:
        console.print("[yellow]未指定要修改的字段[/yellow]")
        raise typer.Exit(1)


@answer_app.command()
def show(
    answer_id: str = typer.Argument(..., help="标准答案 ID"),
):
    """查看单条标准答案详情"""
    store = _get_store()
    a = store.get(answer_id)
    if not a:
        # 也尝试按问题搜索
        results = store.search(answer_id)
        if results:
            a = results[0]
        else:
            console.print(f"[red]未找到标准答案: {answer_id}[/red]")
            raise typer.Exit(1)

    console.print(f"[bold cyan]ID:[/bold cyan] {a.id}")
    console.print(f"[bold cyan]问题:[/bold cyan] {a.question}")
    console.print(f"[bold cyan]答案:[/bold cyan] {a.answer}")
    console.print(f"[bold cyan]类别:[/bold cyan] {a.category or '-'}")
    console.print(f"[bold cyan]标签:[/bold cyan] {', '.join(a.tags) if a.tags else '-'}")
    console.print(f"[bold cyan]匹配策略:[/bold cyan] {a.match_strategy}")
    console.print(f"[bold cyan]来源:[/bold cyan] {a.source or '-'}")
    console.print(f"[bold cyan]生效日期:[/bold cyan] {a.effective_date or '-'}")
    console.print(f"[bold cyan]状态:[/bold cyan] {a.status or 'enabled'}")
    console.print(f"[bold cyan]别名:[/bold cyan] {len(a.aliases)} 个")
    for i, alias in enumerate(a.aliases, 1):
        console.print(f"      [{i}] {alias.question} (相似度: {alias.similarity_score:.3f})")
    console.print(f"[bold cyan]创建时间:[/bold cyan] {a.created_at or '-'}")
    console.print(f"[bold cyan]更新时间:[/bold cyan] {a.updated_at or '-'}")
    console.print(f"[bold cyan]审计日志:[/bold cyan] {len(a.audit_log)} 条")
