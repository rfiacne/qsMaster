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
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
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

    def add_turn(self, question: str, answer: str,
                 sources: list[dict[str, Any]] | None = None) -> None:
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

    使用 JSON 文件存储，支持 CRUD、最近列表、自动清理。
    """

    def __init__(self, store_path: str = "./data/sessions"):
        self.store_path = Path(store_path)
        self.sessions_file = self.store_path / "sessions.json"
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if self.sessions_file.exists():
                try:
                    with open(self.sessions_file, encoding="utf-8") as f:
                        raw = json.load(f)
                    for item in raw:
                        session = Session.from_dict(item)
                        self._sessions[session.id] = session
                    logger.info(f"会话存储加载完成: {len(self._sessions)} 个会话")
                except (OSError, json.JSONDecodeError) as e:
                    logger.error(f"会话存储加载失败: {e}")
            else:
                logger.info("会话存储为空")
            self._loaded = True

    def save(self, session: Session) -> None:
        """保存单个会话"""
        self.store_path.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._sessions[session.id] = session
            self._write_all()

    def save_all(self) -> None:
        """持久化全部会话"""
        self.store_path.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._write_all()

    def _write_all(self) -> None:
        data = [s.to_dict() for s in self._sessions.values()]
        tmp_file = self.sessions_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_file.replace(self.sessions_file)

    def create(self, max_turns: int = 10, max_tokens: int = 4000,
               title: str = "") -> Session:
        """创建新会话"""
        session = Session(
            id=Session.generate_id(),
            max_turns=max_turns,
            max_tokens=max_tokens,
            title=title,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        with self._lock:
            self._sessions[session.id] = session
            self._write_all()
        logger.info(f"创建新会话: {session.id}")
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def list_recent(self, limit: int = 10) -> list[Session]:
        """列出最近的会话（按更新时间倒序）"""
        with self._lock:
            sessions = list(self._sessions.values())
        sessions.sort(key=lambda s: s.updated_at or "", reverse=True)
        return sessions[:limit]

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._sessions:
                return False
            del self._sessions[session_id]
            self._write_all()
        return True

    def clear_old(self, max_days: int = 30) -> int:
        """清理超过保留天数的会话"""
        import time as time_mod
        now = time_mod.time()
        removed = 0
        with self._lock:
            to_remove = []
            for sid, session in self._sessions.items():
                if not session.updated_at:
                    continue
                try:
                    updated = time_mod.strptime(
                        session.updated_at, "%Y-%m-%dT%H:%M:%S"
                    )
                    age_days = (now - time_mod.mktime(updated)) / 86400
                    if age_days > max_days:
                        to_remove.append(sid)
                except (ValueError, OSError):
                    continue
            for sid in to_remove:
                del self._sessions[sid]
                removed += 1
            if removed > 0:
                self._write_all()
        return removed

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    @property
    def is_loaded(self) -> bool:
        return self._loaded
