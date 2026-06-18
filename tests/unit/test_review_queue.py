"""
审核队列 + StandardAnswer 别名/状态 单元测试
"""

from __future__ import annotations

import sys
import tempfile
from unittest import mock

import pytest

embedder_mock = mock.MagicMock()
sys.modules["qa.pipelines.components.embedder"] = embedder_mock

settings_patcher = mock.patch("qa.config.settings.get_settings")
mock_settings = settings_patcher.start()
mock_settings.return_value.llm.api_base_url = "http://test:8000/v1"
mock_settings.return_value.llm.resolved_api_key = "test-key"
mock_settings.return_value.llm.model = "test-model"
mock_settings.return_value.embedding.api_base_url = "http://test:8000/v1"
mock_settings.return_value.embedding.model = "test-model"
mock_settings.return_value.embedding.resolved_api_key = "test-key"

from qa.pipelines.components.early_exit import (  # noqa: E402
    AliasQuestion,
    AuditEntry,
    StandardAnswer,
    StandardAnswerStore,
)
from qa.pipelines.components.review_queue import (  # noqa: E402
    Priority,
    ReviewItem,
    ReviewStats,
    ReviewStatus,
    ReviewStore,
    ReviewWorkflow,
)

# ═══════════════════════════════════════════════════════════════
# AliasQuestion & AuditEntry
# ═══════════════════════════════════════════════════════════════

class TestAliasQuestion:
    def test_default(self):
        a = AliasQuestion()
        assert a.question == ""
        assert a.similarity_score == 0.0

    def test_with_values(self):
        a = AliasQuestion(question="别名问题", similarity_score=0.85)
        assert a.question == "别名问题"
        assert a.similarity_score == 0.85


class TestAuditEntry:
    def test_default(self):
        e = AuditEntry()
        assert e.action == ""
        assert e.operator == "system"

    def test_with_values(self):
        e = AuditEntry(  # noqa: E501
            action="disable", field="status",
            old_value="enabled", new_value="disabled",
            operator="admin",
        )
        assert e.action == "disable"
        assert e.operator == "admin"


# ═══════════════════════════════════════════════════════════════
# StandardAnswer 别名/状态
# ═══════════════════════════════════════════════════════════════

class TestStandardAnswerAliasStatus:
    """StandardAnswer 新增字段"""

    def test_default_status(self):
        a = StandardAnswer(question="Q", answer="A")
        assert a.status == "enabled"

    def test_disabled(self):
        a = StandardAnswer(question="Q", answer="A", status="disabled")
        assert a.status == "disabled"

    def test_aliases_list(self):
        a = StandardAnswer(question="Q", answer="A")
        assert a.aliases == []

    def test_with_aliases(self):
        alias = AliasQuestion(question="别名Q", similarity_score=0.9)
        a = StandardAnswer(question="Q", answer="A", aliases=[alias])
        assert len(a.aliases) == 1
        assert a.aliases[0].question == "别名Q"

    def test_audit_log(self):
        entry = AuditEntry(action="create", field="", operator="admin")
        a = StandardAnswer(question="Q", answer="A", audit_log=[entry])
        assert len(a.audit_log) == 1
        assert a.audit_log[0].action == "create"

    def test_from_dict_with_aliases(self):
        d = {
            "question": "Q", "answer": "A",
            "status": "disabled",
            "aliases": [{"question": "别名Q", "similarity_score": 0.85}],
        }
        a = StandardAnswer.from_dict(d)
        assert a.status == "disabled"
        assert len(a.aliases) == 1
        assert a.aliases[0].similarity_score == 0.85


class TestStandardAnswerStoreAliasStatus:
    """set_status / add_alias / remove_alias / list_enabled"""

    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.store = StandardAnswerStore(store_path=tmpdir)
            self.store.load()
            self.store.add(StandardAnswer(question="Q1", answer="A1", source="test"))
            self.store.add(StandardAnswer(question="Q2", answer="A2", source="test"))
            self.answer_id = list(self.store._answers.keys())[0]
            yield

    def test_list_enabled_all(self):
        enabled = self.store.list_enabled()
        assert len(enabled) == 2

    def test_list_enabled_after_disable(self):
        self.store.set_status(self.answer_id, "disabled")
        enabled = self.store.list_enabled()
        assert len(enabled) == 1

    def test_set_status_invalid(self):
        assert self.store.set_status(self.answer_id, "invalid") is False

    def test_set_status_nonexistent(self):
        assert self.store.set_status("nonexistent", "disabled") is False

    def test_set_status_adds_audit(self):
        self.store.set_status(self.answer_id, "disabled", operator="admin")
        a = self.store.get(self.answer_id)
        assert len(a.audit_log) >= 1
        assert a.audit_log[-1].action == "disable"
        assert a.audit_log[-1].operator == "admin"

    def test_list_by_status(self):
        self.store.set_status(self.answer_id, "candidate")
        disabled = self.store.list_by_status("disabled")
        enabled = self.store.list_by_status("enabled")
        candidate = self.store.list_by_status("candidate")
        assert len(disabled) == 0
        assert len(enabled) == 1
        assert len(candidate) == 1

    def test_add_alias(self):
        added = self.store.add_alias(self.answer_id, "别名问题", similarity_score=0.85)
        assert added is True
        a = self.store.get(self.answer_id)
        assert len(a.aliases) == 1
        assert a.aliases[0].question == "别名问题"
        assert a.aliases[0].similarity_score == 0.85

    def test_add_alias_duplicate(self):
        self.store.add_alias(self.answer_id, "别名问题")
        added = self.store.add_alias(self.answer_id, "别名问题")
        assert added is True  # 不报错，但不会重复添加
        a = self.store.get(self.answer_id)
        assert len(a.aliases) == 1  # 只有一个

    def test_add_alias_normalization(self):
        """带空白的别名应通过标准化去重"""
        self.store.add_alias(self.answer_id, "别名 问题")
        self.store.add_alias(self.answer_id, "  别名 问题  ")  # noqa: F841
        a = self.store.get(self.answer_id)
        assert len(a.aliases) == 1

    def test_remove_alias(self):
        self.store.add_alias(self.answer_id, "别名问题")
        removed = self.store.remove_alias(self.answer_id, "别名问题")
        assert removed is True
        a = self.store.get(self.answer_id)
        assert len(a.aliases) == 0

    def test_remove_nonexistent_alias(self):
        assert self.store.remove_alias(self.answer_id, "不存在的别名") is False

    def test_get_by_alias(self):
        self.store.add_alias(self.answer_id, "别名问题")
        a = self.store.get_by_alias("别名问题")
        assert a is not None
        assert a.id == self.answer_id

    def test_get_by_alias_disabled_not_found(self):
        self.store.add_alias(self.answer_id, "别名问题")
        self.store.set_status(self.answer_id, "disabled")
        a = self.store.get_by_alias("别名问题")
        assert a is None

    def test_get_by_alias_normalized(self):
        self.store.add_alias(self.answer_id, "别名 问题？")
        a = self.store.get_by_alias("  别名 问题?  ")
        assert a is not None


# ═══════════════════════════════════════════════════════════════
# ReviewItem & ReviewStatus
# ═══════════════════════════════════════════════════════════════

class TestReviewItem:
    def test_default(self):
        item = ReviewItem()
        assert item.status == "pending"
        assert item.priority == "normal"

    def test_from_dict_roundtrip(self):
        original = ReviewItem(
            question="测试问题", answer="测试回答",
            faithfulness_score=0.3, faithfulness_result="fail",
            priority="high",
        )
        d = original.to_dict()
        restored = ReviewItem.from_dict(d)
        assert restored.question == "测试问题"
        assert restored.faithfulness_score == 0.3
        assert restored.priority == "high"

    def test_with_label(self):
        item = ReviewItem(
            question="Q", answer="A", status="reviewed",
            label="correct", reviewer="admin",
            review_comment="正确",
        )
        assert item.status == "reviewed"
        assert item.label == "correct"
        assert item.reviewer == "admin"


class TestReviewStatus:
    def test_values(self):
        assert ReviewStatus.PENDING == "pending"
        assert ReviewStatus.CORRECT == "correct"
        assert ReviewStatus.ARCHIVED == "archived"


class TestPriority:
    def test_values(self):
        assert Priority.HIGH == "high"
        assert Priority.NORMAL == "normal"


class TestReviewStats:
    def test_default(self):
        s = ReviewStats()
        assert s.total == 0
        assert s.completion_rate == 0.0

    def test_with_data(self):
        s = ReviewStats(total=10, pending=3, correct=5, partial=1, incorrect=1, completion_rate=0.7)
        assert s.completion_rate == 0.7


# ═══════════════════════════════════════════════════════════════
# ReviewStore
# ═══════════════════════════════════════════════════════════════

class TestReviewStore:
    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.store = ReviewStore(store_path=tmpdir)
            self.store.load()
            yield

    def test_empty(self):
        assert self.store.count() == 0

    def test_add_item(self):
        item = ReviewItem(question="Q", answer="A")
        item_id = self.store.add(item)
        assert len(item_id) == 16
        assert self.store.count() == 1

    def test_get_item(self):
        item = ReviewItem(question="Q", answer="A")
        item_id = self.store.add(item)
        retrieved = self.store.get(item_id)
        assert retrieved is not None
        assert retrieved.question == "Q"

    def test_get_nonexistent(self):
        assert self.store.get("nonexistent") is None

    def test_list_all(self):
        self.store.add(ReviewItem(question="Q1", answer="A1"))
        self.store.add(ReviewItem(question="Q2", answer="A2"))
        items = self.store.list_all()
        assert len(items) == 2

    def test_list_by_status(self):
        i1 = ReviewItem(question="Q1", answer="A1", status="pending")
        i2 = ReviewItem(question="Q2", answer="A2", status="reviewed")
        self.store.add(i1)
        self.store.add(i2)
        pending = self.store.list_all(status="pending")
        reviewed = self.store.list_all(status="reviewed")
        assert len(pending) == 1
        assert len(reviewed) == 1

    def test_priority_sorting(self):
        """HIGH 优先排在 NORMAL 前面"""
        i1 = ReviewItem(question="Q1", priority="normal", created_at="2024-01-01T00:00:00")
        i2 = ReviewItem(question="Q2", priority="high", created_at="2024-01-02T00:00:00")
        self.store.add(i1)
        self.store.add(i2)
        items = self.store.list_all()
        assert items[0].priority == "high"
        assert items[1].priority == "normal"

    def test_remove_item(self):
        item = ReviewItem(question="Q", answer="A")
        item_id = self.store.add(item)
        assert self.store.remove(item_id) is True
        assert self.store.count() == 0

    def test_remove_nonexistent(self):
        assert self.store.remove("nonexistent") is False

    def test_get_stats(self):
        i1 = ReviewItem(question="Q1", status="pending")
        i2 = ReviewItem(question="Q2", label="correct", status="reviewed")
        i3 = ReviewItem(question="Q3", label="incorrect", status="reviewed")
        self.store.add(i1)
        self.store.add(i2)
        self.store.add(i3)
        stats = self.store.get_stats()
        assert stats.total == 3
        assert stats.pending == 1
        assert stats.correct == 1
        assert stats.incorrect == 1
        assert stats.completion_rate == pytest.approx(0.6667, 0.01)

    def test_persistence(self):
        import shutil
        import tempfile
        tmpdir = tempfile.mkdtemp()
        try:
            store1 = ReviewStore(store_path=tmpdir)
            store1.load()
            store1.add(ReviewItem(question="Q", answer="A"))
            store1.save()

            store2 = ReviewStore(store_path=tmpdir)
            store2.load()
            assert store2.count() == 1
        finally:
            shutil.rmtree(tmpdir)


# ═══════════════════════════════════════════════════════════════
# ReviewWorkflow
# ═══════════════════════════════════════════════════════════════

class TestReviewWorkflow:
    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.workflow = ReviewWorkflow(store_path=tmpdir)
            self.workflow.ensure_loaded()
            yield

    def test_add_item_default_priority(self):
        item_id = self.workflow.add_item(question="Q", answer="A")
        assert item_id is not None
        assert self.workflow.store.count() == 1

    def test_add_item_high_priority(self):
        item_id = self.workflow.add_item(
            question="Q", answer="A",
            faithfulness_score=0.2, faithfulness_result="fail",
            priority="high",
        )
        item = self.workflow.store.get(item_id)
        assert item.priority == "high"
        assert item.faithfulness_score == 0.2

    def test_label_correct(self):
        item_id = self.workflow.add_item(question="Q", answer="A")
        result = self.workflow.label(item_id, "correct", reviewer="admin", comment="OK")
        assert result is True
        item = self.workflow.store.get(item_id)
        assert item.label == "correct"
        assert item.reviewer == "admin"
        assert item.review_comment == "OK"

    def test_label_partial(self):
        item_id = self.workflow.add_item(question="Q", answer="A")
        assert self.workflow.label(item_id, "partial") is True

    def test_label_incorrect(self):
        item_id = self.workflow.add_item(question="Q", answer="A")
        assert self.workflow.label(item_id, "incorrect") is True

    def test_label_invalid(self):
        item_id = self.workflow.add_item(question="Q", answer="A")
        assert self.workflow.label(item_id, "invalid_label") is False

    def test_label_nonexistent(self):
        assert self.workflow.label("nonexistent", "correct") is False

    def test_label_twice_rejected(self):
        item_id = self.workflow.add_item(question="Q", answer="A")
        assert self.workflow.label(item_id, "correct") is True
        assert self.workflow.label(item_id, "incorrect") is False  # 不可重复标注

    def test_get_stats(self):
        i1 = self.workflow.add_item(question="Q1", answer="A1")
        i2 = self.workflow.add_item(question="Q2", answer="A2")
        self.workflow.add_item(question="Q3", answer="A3", priority="high")  # noqa: F841
        self.workflow.label(i1, "correct")
        self.workflow.label(i2, "incorrect")
        stats = self.workflow.get_stats()
        assert stats.total == 3
        assert stats.pending == 1
        assert stats.correct == 1
        assert stats.incorrect == 1

    def test_archive_old(self):
        import time
        item_id = self.workflow.add_item(question="Q", answer="A")
        # 设置创建时间为 100 天前
        old_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 100 * 86400))
        with self.workflow.store._lock:
            self.workflow.store._items[item_id].created_at = old_time
        archived = self.workflow.archive_old(max_days=30)
        assert archived == 1
        item = self.workflow.store.get(item_id)
        assert item.status == "archived"
        assert item.archived_at != ""


class TestAutoConvert:
    """语义匹配自动入库 (FR-007~FR-010)"""

    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.store_path = tmpdir
            # StandardAnswerStore
            from qa.pipelines.components.early_exit import StandardAnswerStore
            self.std_store = StandardAnswerStore(store_path=tmpdir)
            self.std_store.load()
            # ReviewWorkflow
            self.workflow = ReviewWorkflow(
                store_path=tmpdir,
                semantic_threshold=0.8,
                candidate_confirm_count=1,
            )
            self.workflow.ensure_loaded()
            yield

    def test_no_existing_answers_creates_candidate(self):
        """无现有标准答案时创建 candidate"""
        self.workflow._auto_convert("新问题", self.std_store)
        candidates = self.std_store.list_by_status("candidate")
        assert len(candidates) == 1
        assert candidates[0].question == "新问题"

    @mock.patch.object(ReviewWorkflow, "_embed_question")
    def test_below_threshold_creates_candidate(self, mock_embed):
        """相似度低于阈值时创建 candidate"""
        from qa.pipelines.components.early_exit import StandardAnswer
        self.std_store.add(StandardAnswer(question="现有问题", answer="现有答案", source="test"))
        aid = list(self.std_store._answers.keys())[0]
        # 使用正交向量确保相似度为 0
        mock_embed.return_value = [1.0, 0.0, 0.0]
        self.workflow._embedding_cache = {aid: [0.0, 1.0, 0.0]}
        self.workflow._auto_convert("完全不同的问题", self.std_store)
        candidates = self.std_store.list_by_status("candidate")
        assert len(candidates) == 1, f"Expected 1 candidate, got {len(candidates)}"
        # 现有答案应没有别名
        existing = self.std_store.get(aid)
        assert len(existing.aliases) == 0

    @mock.patch.object(ReviewWorkflow, "_embed_question")
    def test_above_threshold_adds_alias(self, mock_embed):
        """相似度高于阈值时添加别名"""
        from qa.pipelines.components.early_exit import StandardAnswer
        self.std_store.add(StandardAnswer(question="沪深交易所清算", answer="T+1", source="test"))
        # mock 嵌入：返回高度相似的向量
        aid = list(self.std_store._answers.keys())[0]
        mock_embed.return_value = [0.12, 0.22, 0.31]
        self.workflow._embedding_cache = {aid: [0.1, 0.2, 0.3]}
        self.workflow._auto_convert("沪深交易所T+1清算", self.std_store)
        existing = self.std_store.get(aid)
        assert len(existing.aliases) == 1
        assert existing.aliases[0].similarity_score >= 0.8

    @mock.patch.object(ReviewWorkflow, "_embed_question")
    def test_label_correct_triggers_auto_convert(self, mock_embed):
        """label('correct') 应触发自动转换"""
        from qa.pipelines.components.early_exit import StandardAnswer
        self.std_store.add(StandardAnswer(question="现有问题", answer="现有答案", source="test"))
        aid = list(self.std_store._answers.keys())[0]
        mock_embed.return_value = [0.12, 0.22, 0.31]
        self.workflow._embedding_cache = {aid: [0.1, 0.2, 0.3]}

        item_id = self.workflow.add_item(question="与现有相似的问题", answer="答案")
        result = self.workflow.label(
            item_id, "correct",
            standard_answer_store=self.std_store,
        )
        assert result is True
        # 应在现有答案上添加别名
        existing = self.std_store.get(aid)
        assert len(existing.aliases) == 1

    def test_label_correct_no_std_store(self):
        """不传 standard_answer_store 时不应触发转换"""
        item_id = self.workflow.add_item(question="问题", answer="答案")
        result = self.workflow.label(item_id, "correct")
        assert result is True

    def test_label_correct_auto_convert_false(self):
        """auto_convert=False 时不应触发转换"""
        from qa.pipelines.components.early_exit import StandardAnswer
        self.std_store.add(StandardAnswer(question="Q", answer="A", source="test"))
        item_id = self.workflow.add_item(question="问题", answer="答案")
        result = self.workflow.label(item_id, "correct", auto_convert=False)
        assert result is True

    @mock.patch.object(ReviewWorkflow, "_embed_question")
    def test_cosine_similarity_utility(self, mock_embed):
        """_cosine_similarity 工具方法"""
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        assert self.workflow._cosine_similarity(a, b) == 1.0
        assert self.workflow._cosine_similarity(a, [0.0, 1.0, 0.0]) == 0.0
        assert self.workflow._cosine_similarity([], []) == 0.0
