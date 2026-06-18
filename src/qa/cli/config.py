"""
CLI 配置管理命令 — qa config
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from qa.config.settings import Settings

console = Console()
config_app = typer.Typer(name="config", help="配置管理")


@config_app.command()
def show(
    config: str | None = typer.Option(
        None, "--config", help="配置文件路径（默认 ~/.qa/config.yaml）"
    ),
):
    """显示当前配置"""
    settings = Settings.load(config)

    table = Table(title="当前配置", title_style="bold cyan")
    table.add_column("Section", style="yellow")
    table.add_column("Key", style="green")
    table.add_column("Value", style="white")

    data = settings.as_dict()
    for section, kv in data.items():
        for i, (key, value) in enumerate(kv.items()):
            table.add_row(
                section if i == 0 else "",
                key,
                str(value),
            )

    console.print(table)
    console.print(f"\n配置来源: [bold]{config or settings.config_path}[/bold]")


@config_app.command()
def init(
    config: str | None = typer.Option(
        None, "--config", help="配置文件路径（默认 ~/.qa/config.yaml）"
    ),
    force: bool = typer.Option(False, "--force", help="覆盖已有配置文件"),
):
    """初始化默认配置文件"""
    target = Path(config or Path.home() / ".qa" / "config.yaml")

    if target.exists() and not force:
        console.print(f"[yellow]配置文件已存在: {target}[/yellow]")
        console.print("使用 --force 覆盖")
        raise typer.Exit(1)

    target.parent.mkdir(parents=True, exist_ok=True)

    default_config = """# Securities QA Agent 配置
# 创建于 {name}

llm:
  api_base_url: "http://internal-llm:8000/v1"
  model: "gpt-4"
  api_key_env: "INTERNAL_API_KEY"
  timeout_seconds: 30

embedding:
  api_base_url: "http://internal-llm:8000/v1"
  model: "bge-m3"
  dimensions: 1024
  timeout_seconds: 30

vector_store:
  type: "turbovec"
  bit_width: 4
  similarity_function: "cosine"
  persist_path: "./data/index"

retrieval:
  top_k: 5
  auto_merge_threshold: 0.5
  block_sizes:
    - 500
    - 100

indexing:
  batch_size: 100
  ocr_enabled: false
"""

    target.write_text(default_config, encoding="utf-8")
    console.print(f"[green]✓[/green] 配置文件已创建: {target}")
    console.print("请编辑配置填入内部 API 地址后使用。")
