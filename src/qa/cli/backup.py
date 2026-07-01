"""
备份/恢复 CLI 命令 — 知识库数据打包

用法:
    qa backup                 # 打包 data/ 到默认路径
    qa backup --output ./qa-backup-20260601.zip
    qa restore ./qa-backup-20260601.zip
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import typer

logger = logging.getLogger(__name__)
backup_app = typer.Typer(name="backup", help="知识库备份与恢复", no_args_is_help=True)


def _get_default_data_dir() -> Path:
    from qa.config.settings import get_settings

    try:
        settings = get_settings()
        persist = Path(settings.vector_store.persist_path)
        return persist.parent if persist.is_file() else persist
    except Exception:
        return Path("./data")


def _get_backup_path() -> Path:
    """生成默认备份文件名: data/qa-backup-YYYYMMDD.zip"""
    date_str = datetime.now().strftime("%Y%m%d")
    return Path(f"./qa-backup-{date_str}.zip")


@backup_app.command(name="create")
def backup(
    output: str = typer.Option("", "--output", "-o", help="备份文件路径（默认自动生成）"),
    data_dir: str = typer.Option("", "--data-dir", "-d", help="数据目录（默认从配置读取）"),
) -> None:
    """打包知识库数据到 ZIP 文件"""
    src = Path(data_dir) if data_dir else _get_default_data_dir()
    if not src.exists():
        typer.echo(f"数据目录不存在: {src}", err=True)
        raise typer.Exit(1)

    dest = Path(output) if output else _get_backup_path()

    typer.echo("📦 正在打包知识库数据...")
    typer.echo(f"   源目录: {src}")
    typer.echo(f"   目标:   {dest}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_zip = Path(tmpdir) / "backup.zip"
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in src.rglob("*"):
                if file_path.is_file():
                    arcname = str(file_path.relative_to(src))
                    zf.write(file_path, arcname)

        shutil.copy2(tmp_zip, dest)

    size_mb = dest.stat().st_size / 1024 / 1024
    typer.echo(f"✅ 备份完成 ({size_mb:.1f}MB): {dest}")


@backup_app.command(name="restore")
def restore(
    backup_file: str = typer.Argument(..., help="备份 ZIP 文件路径"),
    data_dir: str = typer.Option("", "--data-dir", "-d", help="数据目录（默认从配置读取）"),
) -> None:
    """从 ZIP 文件恢复知识库数据"""
    src = Path(backup_file)
    if not src.exists():
        typer.echo(f"备份文件不存在: {src}", err=True)
        raise typer.Exit(1)

    dest = Path(data_dir) if data_dir else _get_default_data_dir()
    typer.echo("📂 正在恢复知识库数据...")
    typer.echo(f"   源文件: {src}")
    typer.echo(f"   目标:   {dest}")

    with zipfile.ZipFile(src, "r") as zf:
        # 安全解压：校验每个成员路径，防止 ZipSlip 路径遍历
        dest = dest.resolve()
        for member in zf.namelist():
            member_path = (dest / member).resolve()
            if not str(member_path).startswith(str(dest)):
                raise typer.BadParameter(f"备份文件包含不安全路径: {member}")
        zf.extractall(dest)

    typer.echo(f"✅ 恢复完成: {dest}")
