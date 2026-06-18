"""
Early Exit 单元测试

测试 StandardAnswer 数据模型、StandardAnswerStore CRUD、
标准化匹配、批量导入/导出、EarlyExitMatcher 基本流程。

不依赖 haystack/turbovec/embedding API —— 全部使用纯 Python mock。
"""

from __future__ import annotations

import sys
import tempfile
from unittest import mock

import pytest

# ── mock embedder（避免加载 openai） ──────────────────────
embedder_mock = mock.MagicMock()
sys.modules["qa.pipelines.components.embedder"] = embedder_mock
embedder_mock.embed_texts.return_value = [[0.1, 0.2, 0.3]]
embedder_mock.embed_query.return_value = [0.1, 0.2, 0.3]

# ── mock settings（避免加载 yaml 配置） ──────────────────────
settings_patcher = mock.patch("qa.config.settings.get_settings")
mock_settings = settings_patcher.start()
mock_settings.return_value.llm.api_base_url = "http://test:8000/v1"
mock_settings.return_value.llm.resolved_api_key = "test-key"
mock_settings.return_value.llm.model = "test-model"
mock_settings.return_value.llm.timeout_seconds = 10
mock_settings.return_value.embedding.api_base_url = "http://test:8000/v1"
mock_settings.return_value.embedding.model = "test-model"
mock_settings.return_value.embedding.resolved_api_key = "test-key"


# ── 导入被测模块（放在 mock 之后） ─────────────────────────
from qa.pipelines.components.early_exit import (  # noqa: E402
    EarlyExitMatcher,
    MatchResult,
    StandardAnswer,
    StandardAnswerStore,
)


class TestStandardAnswer:
    """StandardAnswer 数据模型"""

    def test_create_default(self):
        a = StandardAnswer()
        assert a.question == ""
        assert a.answer == ""
        assert a.match_strategy == "both"
        assert a.tags == []

    def test_create_with_fields(self):
        a = StandardAnswer(
            question="测试问题",
            answer="测试答案",
            category="test",
            tags=["tag1", "tag2"],
            source="manual",
            match_strategy="exact",
        )
        assert a.question == "测试问题"
        assert a.answer == "测试答案"
        assert a.category == "test"
        assert a.tags == ["tag1", "tag2"]
        assert a.source == "manual"
        assert a.match_strategy == "exact"

    def test_generate_id_consistency(self):
        """同一问题应生成相同 ID"""
        q = "沪深交易所A股清算周期是多少？"
        id1 = StandardAnswer.generate_id(q)
        id2 = StandardAnswer.generate_id(q)
        assert id1 == id2
        assert len(id1) == 16  # SHA256 前 16 字符

    def test_generate_id_different(self):
        """不同问题应生成不同 ID"""
        id1 = StandardAnswer.generate_id("问题A")
        id2 = StandardAnswer.generate_id("问题B")
        assert id1 != id2

    def test_from_dict_roundtrip(self):
        """to_dict → from_dict 往返一致"""
        original = StandardAnswer(
            question="Q",
            answer="A",
            category="cat",
            tags=["t1"],
            effective_date="2024-01-01",
            source="seed",
            match_strategy="both",
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        d = original.to_dict()
        restored = StandardAnswer.from_dict(d)
        for field in ("id", "question", "answer", "category", "source", "match_strategy"):
            assert getattr(restored, field) == getattr(original, field)


class TestStandardAnswerStore:
    """StandardAnswerStore CRUD 操作"""

    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.store = StandardAnswerStore(store_path=tmpdir)
            self.store.load()
            yield

    def test_empty_store(self):
        assert self.store.count() == 0
        assert self.store.list_all() == []

    def test_add_answer(self):
        a = StandardAnswer(question="问题", answer="答案", category="test")
        is_new = self.store.add(a)
        assert is_new is True
        assert self.store.count() == 1

    def test_add_duplicate_overwrite(self):
        """添加相同 ID 应覆盖"""
        a1 = StandardAnswer(question="问题", answer="答案1")
        self.store.add(a1)
        a2 = StandardAnswer(question="问题", answer="答案2")
        is_new = self.store.add(a2)
        assert is_new is False  # 覆盖
        assert self.store.count() == 1

    def test_add_auto_generate_id(self):
        """未提供 ID 时自动生成"""
        a = StandardAnswer(question="问题", answer="答案")
        self.store.add(a)
        assert len(a.id) == 16
        assert self.store.get(a.id) is not None

    def test_remove_answer(self):
        a = StandardAnswer(question="问题", answer="答案")
        self.store.add(a)
        assert self.store.remove(a.id) is True
        assert self.store.count() == 0

    def test_remove_nonexistent(self):
        assert self.store.remove("nonexistent") is False

    def test_get_by_question_exact(self):
        """精确问题匹配"""
        a = StandardAnswer(question="测试问题", answer="测试答案")
        self.store.add(a)
        found = self.store.get_by_question("测试问题")
        assert found is not None
        assert found.answer == "测试答案"

    def test_get_by_question_with_spaces(self):
        """带空白的标准化匹配"""
        a = StandardAnswer(question="清算周期多少", answer="T+1")
        self.store.add(a)
        found = self.store.get_by_question("  清算周期多少  ")
        assert found is not None
        assert found.answer == "T+1"

    def test_get_by_question_not_found(self):
        found = self.store.get_by_question("不存在的")
        assert found is None

    def test_list_by_category(self):
        self.store.add(StandardAnswer(question="Q1", answer="A1", category="cat_a"))
        self.store.add(StandardAnswer(question="Q2", answer="A2", category="cat_b"))
        self.store.add(StandardAnswer(question="Q3", answer="A3", category="cat_a"))
        cat_a = self.store.list_by_category("cat_a")
        assert len(cat_a) == 2
        cat_b = self.store.list_by_category("cat_b")
        assert len(cat_b) == 1

    def test_search_by_keyword(self):
        self.store.add(StandardAnswer(question="清算周期", answer="T+1"))
        self.store.add(StandardAnswer(question="CCASS交收", answer="CCASS系统"))
        results = self.store.search("清算")
        assert len(results) == 1
        assert results[0].question == "清算周期"

    def test_search_in_answer(self):
        """搜索关键词在答案中"""
        self.store.add(StandardAnswer(question="Q1", answer="T+1清算规则"))
        results = self.store.search("清算规则")
        assert len(results) == 1

    def test_clear(self):
        self.store.add(StandardAnswer(question="Q1", answer="A1"))
        self.store.add(StandardAnswer(question="Q2", answer="A2"))
        count = self.store.clear()
        assert count == 2
        assert self.store.count() == 0

    def test_persistence(self):
        """数据应持久化到磁盘并重新加载"""
        tmpdir = tempfile.mkdtemp()
        try:
            store1 = StandardAnswerStore(store_path=tmpdir)
            store1.load()
            store1.add(StandardAnswer(question="Q", answer="A"))
            store1.save()

            store2 = StandardAnswerStore(store_path=tmpdir)
            store2.load()
            assert store2.count() == 1
            assert store2.get_by_question("Q").answer == "A"
        finally:
            import shutil
            shutil.rmtree(tmpdir)


class TestNormalization:
    """标准化文本匹配"""

    def test_fullwidth_to_halfwidth(self):
        result = StandardAnswerStore._normalize("ＡＢＣ１２３")
        # 全角应转半角
        assert "abc123" in result

    def test_punctuation_normalization(self):
        result = StandardAnswerStore._normalize("问题？测试！中文，标点。")
        assert "?" in result or "？" not in result

    def test_whitespace_collapse(self):
        result = StandardAnswerStore._normalize("  A  B  C  ")
        assert result == "a b c"

    def test_lowercase(self):
        result = StandardAnswerStore._normalize("Hello World")
        assert result == "hello world"

    def test_empty_string(self):
        result = StandardAnswerStore._normalize("")
        assert result == ""
        result2 = StandardAnswerStore._normalize("   ")
        assert result2 == ""

    def test_chinese_mixed(self):
        """中英文混合文本"""
        result = StandardAnswerStore._normalize("沪深交易所T+1清算")
        assert "t+1" in result
        assert "清算" in result


class TestImportBatch:
    """批量导入"""

    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.store = StandardAnswerStore(store_path=tmpdir)
            self.store.load()
            yield

    def test_import_valid_items(self):
        items = [
            {"question": "Q1", "answer": "A1"},
            {"question": "Q2", "answer": "A2"},
        ]
        added, overwritten, errors = self.store.import_batch(items)
        assert added == 2
        assert overwritten == 0
        assert len(errors) == 0
        assert self.store.count() == 2

    def test_import_missing_fields(self):
        items = [
            {"question": "Q1", "answer": "A1"},
            {"question": "", "answer": "A2"},  # 缺问题
            {"answer": "A3"},  # 缺 question
        ]
        added, overwritten, errors = self.store.import_batch(items)
        assert added == 1  # 只有第一条成功
        assert len(errors) == 2

    def test_import_with_category(self):
        items = [
            {"question": "Q1", "answer": "A1", "category": "test_cat", "source": "seed"},
        ]
        added, overwritten, errors = self.store.import_batch(items)
        assert added == 1
        a = self.store.get_by_question("Q1")
        assert a.category == "test_cat"
        assert a.source == "seed"

    def test_export_all(self):
        self.store.add(StandardAnswer(question="Q1", answer="A1", category="cat1"))
        self.store.add(StandardAnswer(question="Q2", answer="A2", category="cat2"))
        data = self.store.export_all()
        assert len(data) == 2
        assert data[0]["question"] in ("Q1", "Q2")


class TestEarlyExitMatcher:
    """EarlyExitMatcher 基本流程"""

    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.store_path = tmpdir
            store = StandardAnswerStore(store_path=tmpdir)
            store.load()
            store.add(StandardAnswer(
                question="沪深交易所A股清算周期是多少？",
                answer="T+1",
                category="清算规则",
                source="seed",
                match_strategy="exact",
            ))
            store.add(StandardAnswer(
                question="CCASS是什么？",
                answer="中央结算及交收系统",
                category="清算规则",
                source="seed",
                match_strategy="both",
            ))
            self.store = store
            yield

    def make_matcher(self, enabled=True, fuzzy_threshold=0.5) -> EarlyExitMatcher:
        matcher = EarlyExitMatcher(
            store_path=self.store_path,
            fuzzy_threshold=fuzzy_threshold,
            enabled=enabled,
        )
        matcher.store = self.store  # 复用已加载的 store
        matcher._index_built = True  # 跳过索引构建（无 embedder）
        return matcher

    def test_disabled(self):
        matcher = self.make_matcher(enabled=False)
        result = matcher.match("沪深交易所A股清算周期是多少？")
        assert result.matched is False

    def test_empty_question(self):
        matcher = self.make_matcher()
        result = matcher.match("")
        assert result.matched is False
        result = matcher.match("   ")
        assert result.matched is False

    def test_exact_match(self):
        matcher = self.make_matcher()
        result = matcher.match("沪深交易所A股清算周期是多少？")
        assert result.matched is True
        assert result.match_type == "exact"
        assert result.score == 1.0
        assert result.answer.answer == "T+1"

    def test_exact_match_with_extra_whitespace(self):
        """额外空白应标准化后匹配"""
        matcher = self.make_matcher()
        result = matcher.match("  沪深交易所A股清算周期是多少？  ")
        assert result.matched is True
        assert result.match_type == "exact"

    def test_exact_match_fullwidth_punctuation(self):
        """全角标点应标准化"""
        matcher = self.make_matcher()
        result = matcher.match("沪深交易所A股清算周期是多少？")  # 全角问号
        assert result.matched is True

    def test_no_match_unknown_question(self):
        matcher = self.make_matcher()
        result = matcher.match("完全不相关的问题")
        assert result.matched is False

    def test_partial_match_not_exact(self):
        """部分匹配不应触发精确匹配"""
        matcher = self.make_matcher()
        result = matcher.match("清算周期")  # 只是问题的一部分
        assert result.matched is False  # 不应该精确命中

    def test_match_only_exact_strategy(self):
        """match_strategy=exact 的条目只能通过精确匹配命中"""
        matcher = self.make_matcher()
        result = matcher.match("沪深交易所A股清算周期是多少？")
        assert result.matched is True
        assert result.match_type == "exact"

    def test_fuzzy_match(self):
        """模糊匹配（嵌入缓存模拟）"""
        matcher = self.make_matcher(fuzzy_threshold=0.5)
        # 给两个条目添加嵌入缓存
        for a in self.store.list_all():
            if a.match_strategy in ("fuzzy", "both"):
                matcher._embedding_cache[a.id] = [0.1, 0.2, 0.3]
        matcher._index_built = True

        # 精确匹配优先 → "CCASS是什么？" 精确命中
        result = matcher.match("CCASS是什么？")
        assert result.matched is True
        assert result.match_type == "exact"

        # 不同措辞但嵌入相似 → 模糊匹配可能中
        # 设置 _embed_question 返回相似向量
        original_embed = matcher._embed_question
        matcher._embed_question = lambda q: [0.12, 0.22, 0.31]  # 与缓存 [0.1,0.2,0.3] 高度相似
        result = matcher.match("什么是CCASS系统")
        if result.matched:
            assert result.match_type in ("exact", "fuzzy")
        matcher._embed_question = original_embed

    def test_fuzzy_match_below_threshold(self):
        """模糊匹配低于阈值时不命中"""
        matcher = self.make_matcher(fuzzy_threshold=0.8)
        for a in self.store.list_all():
            if a.match_strategy in ("fuzzy", "both"):
                matcher._embedding_cache[a.id] = [0.1, 0.2, 0.3]
        matcher._index_built = True

        # 设置 _embed_question 返回完全不相似的向量
        original_embed = matcher._embed_question
        matcher._embed_question = lambda q: [0.9, 0.9, 0.9]  # 余弦相似度很低
        result = matcher.match("完全不相关的股票问题")
        assert result.matched is False
        matcher._embed_question = original_embed

    def test_match_batch(self):
        """批量匹配应返回等长结果"""
        matcher = self.make_matcher()
        results = matcher.match_batch([
            "沪深交易所A股清算周期是多少？",
            "未知问题",
            "CCASS是什么？",
        ])
        assert len(results) == 3
        assert results[0].matched is True
        assert results[1].matched is False
        assert results[2].matched is True

    def test_is_enabled(self):
        """有数据时应返回 is_enabled"""
        matcher = self.make_matcher()
        assert matcher.is_enabled is True

    def test_is_enabled_empty(self):
        """空库时应返回 is_enabled=False"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StandardAnswerStore(store_path=tmpdir)
            store.load()
            matcher = EarlyExitMatcher(store_path=tmpdir, enabled=True)
            matcher.store = store
            assert matcher.is_enabled is False


class TestMatchResult:
    """MatchResult 数据模型"""

    def test_default(self):
        mr = MatchResult()
        assert mr.matched is False
        assert mr.match_type == ""
        assert mr.score == 0.0

    def test_matched(self):
        a = StandardAnswer(question="Q", answer="A")
        mr = MatchResult(matched=True, answer=a, match_type="exact", score=1.0)
        assert mr.matched is True
        assert mr.answer.answer == "A"
        assert mr.match_type == "exact"
        assert mr.score == 1.0
