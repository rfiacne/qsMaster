"""
术语映射管理 CLI — 管理证券简称→全称映射表

用法:
    qa term list                       # 列出所有术语映射
    qa term add "中登公司" "中国证券登记结算有限责任公司"
    qa term remove "中登公司"
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

logger = logging.getLogger(__name__)
term_app = typer.Typer(name="term", help="术语映射管理", no_args_is_help=True)


def _get_term_map_path() -> Path:
    """获取术语映射表路径"""
    from qa.pipelines.components.query_rewriter import QueryRewriter

    rw = QueryRewriter(enabled=False)
    return Path(rw.term_map_path)


def _load_mappings(path: Path) -> dict[str, str]:
    """加载术语映射表"""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw.get("mappings", raw) if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_mappings(path: Path, mappings: dict[str, str]) -> None:
    """保存术语映射表"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"mappings": mappings}
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    typer.echo(f"已保存 {len(mappings)} 条术语映射到 {path}")


@term_app.command(name="list")
def list_terms(
    search: str = typer.Option("", "--search", "-s", help="搜索关键词"),
):
    """列出所有术语映射"""
    path = _get_term_map_path()
    mappings = _load_mappings(path)

    if not mappings:
        typer.echo("当前无术语映射。")
        return

    if search:
        sl = search.lower()
        filtered = {k: v for k, v in mappings.items() if sl in k.lower() or sl in v.lower()}
    else:
        filtered = mappings

    if not filtered:
        typer.echo(f"未找到匹配 '{search}' 的术语映射。")
        return

    typer.echo(f"\n术语映射表 ({len(filtered)} 条):\n")
    for i, (short, full) in enumerate(sorted(filtered.items(), key=lambda x: -len(x[0])), 1):
        typer.echo(f"  {i:3d}. {short}  →  {full}")


@term_app.command(name="add")
def add_term(
    short: str = typer.Argument(..., help="简称（如 中登公司）"),
    full: str = typer.Argument(..., help="全称（如 中国证券登记结算有限责任公司）"),
):
    """添加或更新术语映射"""
    path = _get_term_map_path()
    mappings = _load_mappings(path)

    is_update = short in mappings
    old_full = mappings.get(short, "")
    mappings[short] = full

    # 按长度降序排序（长词优先匹配）
    sorted_mappings = dict(sorted(mappings.items(), key=lambda x: -len(x[0])))
    _save_mappings(path, sorted_mappings)

    if is_update:
        typer.echo(f"🔄 已更新: {short}  {old_full} → {full}")
    else:
        typer.echo(f"✅ 已添加: {short}  →  {full}")


@term_app.command(name="remove")
def remove_term(
    short: str = typer.Argument(..., help="要删除的简称"),
):
    """删除术语映射"""
    path = _get_term_map_path()
    mappings = _load_mappings(path)

    if short not in mappings:
        typer.echo(f"术语 '{short}' 不存在。", err=True)
        raise typer.Exit(1)

    full = mappings.pop(short)
    _save_mappings(path, mappings)
    typer.echo(f"🗑️  已删除: {short}  →  {full}")


@term_app.command(name="clear")
def clear_terms():
    """清空所有术语映射"""
    path = _get_term_map_path()
    if not path.exists() or not _load_mappings(path):
        typer.echo("术语映射表已为空。")
        return

    typer.confirm("确定要清空所有术语映射？", abort=True)
    _save_mappings(path, {})
    typer.echo("已清空所有术语映射。")
