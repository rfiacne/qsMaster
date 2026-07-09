"""
SQLite 统一持久化存储 — 替换 JSON 全量写放大模式

设计:
  - BaseSQLiteStore: 抽象基类，封装 SQLite 连接池、表创建、行级锁
  - 每个 Store 子类管理一张表，行级写入不阻塞全量读取
  - 旧 JSON 文件自动迁移（启动时检测）

用法:
    class MyStore(BaseSQLiteStore):
        table_name = "my_items"
        schema = "..."
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BaseSQLiteStore:
    """SQLite 持久化存储基类

    线程安全：使用 sqlite3 连接级序列化 + threading.Lock 保护写操作。
    所有写操作行级粒度，读操作无锁并发的 snapshot isolation。
    """

    table_name: str = ""
    create_sql: str = ""

    def __init__(
        self,
        db_path: str | Path,
        auto_migrate_json: str | None = None,
        json_loader: Callable | None = None,
    ):
        """
        Args:
            db_path: SQLite 数据库文件路径
            auto_migrate_json: 可选，旧 JSON 文件路径（存在时自动迁移）
            json_loader: 可选，从 JSON 数据加载初始数据的函数
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

        # 自动迁移旧 JSON 数据
        if auto_migrate_json and json_loader:
            json_path = Path(auto_migrate_json)
            if json_path.exists():
                try:
                    with open(json_path, encoding="utf-8") as f:
                        data = json.load(f)
                    if data and json_loader(data):
                        logger.info(
                            f"旧 JSON 数据已自动迁移到 SQLite: {json_path}"
                        )
                        backup = json_path.with_suffix(".json.bak")
                        json_path.rename(backup)
                except Exception as e:
                    logger.warning(f"JSON 自动迁移失败（跳过）: {e}")

    def _get_conn(self) -> sqlite3.Connection:
        """获取 SQLite 连接（惰性初始化）"""
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    self._conn = sqlite3.connect(
                        str(self._db_path),
                        check_same_thread=False,
                    )
                    self._conn.row_factory = sqlite3.Row
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA synchronous=NORMAL")
                    if self.create_sql:
                        self._conn.execute(self.create_sql)
                    self._conn.commit()
        return self._conn

    def close(self) -> None:
        """关闭连接"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> BaseSQLiteStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """执行 SQL（自动重连保护）"""
        try:
            return self._get_conn().execute(sql, params)
        except sqlite3.ProgrammingError as e:
            if "closed" in str(e):
                with self._lock:
                    self._conn = None
                return self._get_conn().execute(sql, params)
            raise

    def _execute_many(self, sql: str, params_list: list[tuple]) -> None:
        """批量执行 SQL"""
        conn = self._get_conn()
        conn.executemany(sql, params_list)
        conn.commit()
