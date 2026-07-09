"""
持久化会话记忆 — SessionStore + Session 数据模型

为多轮对话提供持久化存储和上下文窗口管理。

功能:
  - Session 数据模型（含对话历史、上下文窗口）
  - SessionStore: JSON 文件持久化（CRUD + 最近列表）
  - 上下文窗口：自动截断到最近 N 轮 / max_tokens

用法:
    store = SessionStore()
    store.load()
    session = store.create()
    session.add_turn("问题", "回答", sources)
    store.save(session)
    restored = store.get(session.id)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Turn:
    """单轮对话记录"""

    question: str = ""
    answer: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""

    def token_count(self) -> int:
        """估算 token 数（中文字符 ~1.5 token/字，英文 ~0.25 token/字符）"""
        text = f"{self.question} {self.answer}"
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars * 0.25) + 100


@dataclass
class Session:
    """会话

    Attributes:
        id: 会话唯一标识
        turns: 对话轮次列表（最新在末尾）
        max_turns: 最大保留轮数（超出自动丢弃最早）
        max_tokens: 最大 token 数（超出自动丢弃最早轮次）
        created_at: 创建时间
        updated_at: 最后更新时间
        title: 会话标题（从首轮问题自动生成）
        metadata: 扩展元数据（如过滤条件）
    """

    id: str = ""
    turns: list[Turn] = field(default_factory=list)
    max_turns: int = 10
    max_tokens: int = 4000
    created_at: str = ""
    updated_at: str = ""
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_turn(
        self, question: str, answer: str, sources: list[dict[str, Any]] | None = None
    ) -> None:
        """添加一轮对话并自动截断上下文"""
        turn = Turn(
            question=question,
            answer=answer,
            sources=sources or [],
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self.turns.append(turn)
        self.updated_at = turn.timestamp
        if not self.title and question:
            self.title = question[:50] + ("..." if len(question) > 50 else "")
        self._truncate()

    def get_recent_turns(self, max_turns: int = 5) -> list[Turn]:
        """获取最近 N 轮对话（LLM 上下文用）"""
        return self.turns[-max_turns:]

    def get_context_text(self, max_turns: int = 5) -> str:
        """获取格式化的上下文文本（注入 LLM prompt 用）"""
        recent = self.get_recent_turns(max_turns)
        if not recent:
            return ""
        parts = []
        for i, turn in enumerate(recent, 1):
            parts.append(f"[历史对话 {i}]")
            parts.append(f"用户: {turn.question}")
            parts.append(f"助手: {turn.answer[:200]}")
        return "\n".join(parts)

    def _truncate(self) -> None:
        """按 max_turns 和 max_tokens 截断"""
        # 按轮数截断
        while len(self.turns) > self.max_turns:
            self.turns.pop(0)
        # 按 token 数截断
        while self._total_tokens() > self.max_tokens and len(self.turns) > 1:
            self.turns.pop(0)

    def _total_tokens(self) -> int:
        return sum(t.token_count() for t in self.turns)

    @staticmethod
    def generate_id() -> str:
        import secrets

        return secrets.token_hex(6)  # 12-char hex, CSPRNG

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        turns = [Turn(**t) if isinstance(t, dict) else t for t in data.get("turns", [])]
        return cls(
            id=data.get("id", ""),
            turns=turns,
            max_turns=data.get("max_turns", 10),
            max_tokens=data.get("max_tokens", 4000),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            title=data.get("title", ""),
            metadata=data.get("metadata", {}),
        )


class SessionStore:
    """会话持久化存储

    使用 SQLite 存储，行级写入，避免 JSON 全量写放大。
    旧 JSON 文件 data/sessions/sessions.json 自动迁移到 db_path。
    """

    def __init__(self, store_path: str = "./data/sessions"):
        self._db_path = Path(store_path) / "sessions.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._loaded = True  # SQLite always loaded

        # 自动迁移旧 JSON 数据
        json_path = Path(store_path) / "sessions.json"
        if json_path.exists() and not self._db_path.exists():
            try:
                with open(json_path, encoding="utf-8") as f:
                    raw = json.load(f)
                if raw:
                    self._migrate_from_json(raw)
                    backup = json_path.with_suffix(".json.bak")
                    json_path.rename(backup)
                    logger.info(f"旧 JSON 会话数据已自动迁移到 SQLite: {json_path} → {self._db_path}")
            except Exception as e:
                logger.warning(f"JSON 会话自动迁移失败（跳过）: {e}")

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    self._conn = sqlite3.connect(
                        str(self._db_path), check_same_thread=False
                    )
                    self._conn.row_factory = sqlite3.Row
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA synchronous=NORMAL")
                    self._conn.execute("""
                        CREATE TABLE IF NOT EXISTS sessions (
                            id TEXT PRIMARY KEY,
                            turns TEXT NOT NULL DEFAULT '[]',
                            max_turns INTEGER NOT NULL DEFAULT 10,
                            max_tokens INTEGER NOT NULL DEFAULT 4000,
                            created_at TEXT NOT NULL DEFAULT '',
                            updated_at TEXT NOT NULL DEFAULT '',
                            title TEXT NOT NULL DEFAULT '',
                            metadata TEXT NOT NULL DEFAULT '{}'
                        )
                    """)
                    self._conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_sessions_updated
                        ON sessions(updated_at)
                    """)
                    self._conn.commit()
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> SessionStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _migrate_from_json(self, raw: list[dict]) -> None:
        conn = self._get_conn()
        for item in raw:
            conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (id, turns, max_turns, max_tokens, created_at, updated_at, title, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.get("id", ""),
                    json.dumps(item.get("turns", []), ensure_ascii=False),
                    item.get("max_turns", 10),
                    item.get("max_tokens", 4000),
                    item.get("created_at", ""),
                    item.get("updated_at", ""),
                    item.get("title", ""),
                    json.dumps(item.get("metadata", {}), ensure_ascii=False),
                ),
            )
        conn.commit()
        logger.info(f"会话数据自动迁移完成: {len(raw)} 条记录")

    def load(self) -> None:
        """兼容原接口 — SQLite 无需加载到内存"""
        pass

    def save(self, session: Session) -> None:
        """保存单个会话（行级写入）"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(
            [asdict(t) for t in session.turns],
            ensure_ascii=False,
        )
        meta = json.dumps(session.metadata, ensure_ascii=False)
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (id, turns, max_turns, max_tokens, created_at, updated_at, title, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id,
                    data,
                    session.max_turns,
                    session.max_tokens,
                    session.created_at,
                    session.updated_at,
                    session.title,
                    meta,
                ),
            )
            conn.commit()

    def flush(self) -> None:
        """兼容原接口 — SQLite 行级写入即时持久化"""
        logger.debug("SQLite 已即时持久化，无需 flush")

    def save_all(self) -> None:
        """兼容原接口 — SQLite 单个保存即时落盘"""
        logger.debug("SQLite 已即时持久化，无需 save_all")

    def create(self, max_turns: int = 10, max_tokens: int = 4000, title: str = "") -> Session:
        """创建新会话"""
        session = Session(
            id=Session.generate_id(),
            max_turns=max_turns,
            max_tokens=max_tokens,
            title=title,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self.save(session)
        logger.info(f"创建新会话: {session.id}")
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    def list_recent(self, limit: int = 10) -> list[Session]:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
        return [self._row_to_session(r) for r in rows]

    def delete(self, session_id: str) -> bool:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
        return cur.rowcount > 0

    def clear_old(self, max_days: int = 30) -> int:
        import time as time_mod

        now = time_mod.time()
        cutoff = time_mod.strftime(
            "%Y-%m-%dT%H:%M:%S",
            time_mod.localtime(now - max_days * 86400),
        )
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)
            )
            conn.commit()
        removed = cur.rowcount
        if removed > 0:
            logger.info(f"会话清理: {removed} 个超过 {max_days} 天")
        return removed

    def count(self) -> int:
        with self._lock:
            cur = self._get_conn().execute("SELECT COUNT(*) AS cnt FROM sessions")
            row = cur.fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        import dataclasses

        turns_raw = json.loads(row["turns"] or "[]")
        turn_fields = {f.name for f in dataclasses.fields(Turn)}
        turns = [
            Turn(**{k: v for k, v in t.items() if k in turn_fields})
            if isinstance(t, dict)
            else t
            for t in turns_raw
        ]
        meta_raw = json.loads(row["metadata"] or "{}")
        return Session(
            id=row["id"],
            turns=turns,
            max_turns=row["max_turns"],
            max_tokens=row["max_tokens"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            title=row["title"],
            metadata=meta_raw,
        )

    @property
    def is_loaded(self) -> bool:
        return True
