"""
SessionStore 持久化会话 + 上下文窗口 单元测试
"""

from __future__ import annotations

import tempfile

import pytest

from qa.pipelines.components.session_store import (  # noqa: E402
    Session,
    SessionStore,
    Turn,
)


class TestTurn:
    def test_default(self):
        t = Turn()
        assert t.question == ""
        assert t.answer == ""

    def test_token_count(self):
        t = Turn(question="测试问题", answer="测试答案")
        assert t.token_count() > 0

    def test_token_count_empty(self):
        t = Turn()
        assert t.token_count() == 100  # 基础值


class TestSession:
    def test_default(self):
        s = Session()
        assert s.turns == []
        assert s.max_turns == 10
        assert s.max_tokens == 4000

    def test_add_turn(self):
        s = Session()
        s.add_turn("问题1", "回答1")
        assert len(s.turns) == 1
        assert s.turns[0].question == "问题1"
        assert s.title == "问题1"

    def test_title_generated(self):
        s = Session()
        s.add_turn("这是一个很长的问题标题超过五十字了吗" * 3, "回答")
        assert len(s.title) <= 53  # 50 + "..."

    def test_get_recent_turns(self):
        s = Session()
        for i in range(10):
            s.add_turn(f"问题{i}", f"回答{i}")
        recent = s.get_recent_turns(max_turns=3)
        assert len(recent) == 3
        assert recent[0].question == "问题7"

    def test_truncate_by_turns(self):
        s = Session(max_turns=3)
        for i in range(5):
            s.add_turn(f"问题{i}", f"回答{i}")
        assert len(s.turns) == 3
        assert s.turns[0].question == "问题2"

    def test_truncate_by_tokens(self):
        s = Session(max_turns=100, max_tokens=200)
        s.add_turn("Q1", "A1")  # ~100 tokens
        s.add_turn("Q2", "A2")  # ~100 tokens → 略超
        assert len(s.turns) >= 1  # 至少保留最后一轮

    def test_get_context_text(self):
        s = Session()
        s.add_turn("清算流程是什么？", "T+1清算制度")
        text = s.get_context_text(max_turns=5)
        assert "清算流程" in text
        assert "T+1清算" in text
        assert "历史对话" in text

    def test_get_context_text_empty(self):
        s = Session()
        assert s.get_context_text() == ""

    def test_from_dict_roundtrip(self):
        original = Session(id="test123", title="测试")
        original.add_turn("Q", "A")
        d = original.to_dict()
        restored = Session.from_dict(d)
        assert restored.id == "test123"
        assert len(restored.turns) == 1
        assert restored.turns[0].question == "Q"

    def test_generate_id_unique(self):
        ids = {Session.generate_id() for _ in range(100)}
        assert len(ids) == 100  # 全部唯一


class TestSessionStore:
    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.store = SessionStore(store_path=tmpdir)
            self.store.load()
            yield

    def test_empty(self):
        assert self.store.count() == 0

    def test_create(self):
        s = self.store.create(title="测试会话")
        assert len(s.id) == 12
        assert s.title == "测试会话"
        assert self.store.count() == 1

    def test_get(self):
        s = self.store.create()
        retrieved = self.store.get(s.id)
        assert retrieved is not None
        assert retrieved.id == s.id

    def test_get_nonexistent(self):
        assert self.store.get("nonexistent") is None

    def test_save_and_load(self):
        s = self.store.create()
        s.add_turn("问题", "回答")
        self.store.save(s)

        retrieved = self.store.get(s.id)
        assert len(retrieved.turns) == 1

    def test_list_recent(self):
        s_a = self.store.create(title="会话A")
        # 手动设置不同的 updated_at 来确保排序
        s_a.updated_at = "2024-01-01T00:00:00"
        self.store.save(s_a)
        s_b = self.store.create(title="会话B")
        s_b.updated_at = "2024-06-01T00:00:00"
        self.store.save(s_b)
        recent = self.store.list_recent(limit=2)
        assert len(recent) == 2
        # updated_at 更新的优先
        assert recent[0].title == "会话B"

    def test_delete(self):
        s = self.store.create()
        assert self.store.delete(s.id) is True
        assert self.store.count() == 0

    def test_delete_nonexistent(self):
        assert self.store.delete("nonexistent") is False

    def test_persistence(self):
        import shutil
        import tempfile

        tmpdir = tempfile.mkdtemp()
        try:
            store1 = SessionStore(store_path=tmpdir)
            s = store1.create(title="持久化测试")
            s.add_turn("Q", "A")
            store1.save(s)
            store1.close()

            store2 = SessionStore(store_path=tmpdir)
            assert store2.count() == 1
            restored = store2.get(s.id)
            assert restored.title == "持久化测试"
            assert len(restored.turns) == 1
            store2.close()
        finally:
            shutil.rmtree(tmpdir)

    def test_clear_old(self):
        import time as tm

        s = self.store.create()
        old_time = tm.strftime("%Y-%m-%dT%H:%M:%S", tm.gmtime(tm.time() - 60 * 86400))
        s.updated_at = old_time
        self.store.save(s)
        removed = self.store.clear_old(max_days=30)
        assert removed == 1
        assert self.store.count() == 0


class TestSlidingWindow:
    """M4 FR-003: Sliding Window 截断"""

    def test_sliding_window_by_turns(self):
        """超过 max_turns 时自动丢弃最早轮次"""
        s = Session(max_turns=5)
        for i in range(8):
            s.add_turn(f"问题{i}", f"回答{i}")
        assert len(s.turns) == 5
        assert s.turns[0].question == "问题3"

    def test_sliding_window_by_tokens(self):
        """超过 max_tokens 时自动截断"""
        s = Session(max_turns=100, max_tokens=300)
        for i in range(10):
            s.add_turn(f"问题{i}" * 10, f"回答{i}" * 10)
        # 应至少保留最后 1 轮
        assert len(s.turns) >= 1
        # 最后一轮应保留
        assert "9" in s.turns[-1].question

    def test_get_recent_turns_default_5(self):
        """get_recent_turns 默认返回最近 5 轮"""
        s = Session()
        for i in range(10):
            s.add_turn(f"Q{i}", f"A{i}")
        recent = s.get_recent_turns()
        assert len(recent) == 5
        assert recent[-1].question == "Q9"

    def test_context_text_format(self):
        """上下文文本包含历史对话标记"""
        s = Session()
        s.add_turn("清算是什么？", "T+1清算制度")
        s.add_turn("具体流程呢？", "资金划转和证券交收")
        text = s.get_context_text(max_turns=5)
        assert "[历史对话 1]" in text
        assert "[历史对话 2]" in text
        assert "清算" in text


class TestSessionIsolation:
    """M4 FR-005: 会话隔离"""

    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.store = SessionStore(store_path=tmpdir)
            self.store.load()
            yield

    def test_sessions_isolated(self):
        """不同会话的数据互不影响"""
        s1 = self.store.create(title="会话1")
        s2 = self.store.create(title="会话2")
        s1.add_turn("会话1的问题", "会话1的回答")
        s2.add_turn("会话2的问题", "会话2的回答")
        self.store.save(s1)
        self.store.save(s2)

        r1 = self.store.get(s1.id)
        r2 = self.store.get(s2.id)
        assert r1.turns[0].question == "会话1的问题"
        assert r2.turns[0].question == "会话2的问题"
        assert len(r1.turns) == 1
        assert len(r2.turns) == 1

    def test_delete_one_does_not_affect_other(self):
        """删除一个会话不影响其他会话"""
        s1 = self.store.create(title="保留")
        s2 = self.store.create(title="删除")
        self.store.delete(s2.id)
        assert self.store.get(s1.id) is not None
        assert self.store.get(s2.id) is None
        assert self.store.count() == 1
