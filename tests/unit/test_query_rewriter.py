"""
QueryRewriter 单元测试（M6 review finding）

覆盖:
- QueryRewriteResult 数据类
- 术语归一化 _normalize_terms
- 多意图判断 _should_decompose
- LLM 响应解析 _parse_llm_response
- rewrite 主流程（透传/归一化/分解/回退）
- 工厂函数 create_query_rewriter
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from qa.pipelines.components.query_rewriter import (
    QueryRewriter,
    QueryRewriteResult,
    create_query_rewriter,
)

# ─── QueryRewriteResult 数据类 ────────────────────────


class TestQueryRewriteResult:
    def test_default(self):
        r = QueryRewriteResult()
        assert r.original == ""
        assert r.rewritten == ""
        assert r.sub_questions == []
        assert r.was_rewritten is False
        assert r.rewrite_time_ms == 0.0
        assert r.rewrite_method == ""

    def test_with_values(self):
        r = QueryRewriteResult(
            original="什么是CCASS?",
            rewritten="什么是中央结算及交收系统?",
            sub_questions=["CCASS定义"],
            was_rewritten=True,
            rewrite_time_ms=5.0,
            rewrite_method="term_normalize",
        )
        assert r.original == "什么是CCASS?"
        assert r.rewritten == "什么是中央结算及交收系统?"
        assert len(r.sub_questions) == 1
        assert r.was_rewritten is True

    def test_sub_questions_default_list(self):
        """sub_questions 是独立的列表副本"""
        r = QueryRewriteResult()
        r.sub_questions.append("q1")
        assert len(r.sub_questions) == 1

    def test_empty_original(self):
        r = QueryRewriteResult(original="")
        assert r.was_rewritten is False


# ─── 初始化 ────────────────────────────────────────────


class TestQueryRewriterInit:
    def test_disabled_bypass(self):
        """禁用时 rewrite 透传"""
        rw = QueryRewriter(enabled=False)
        result = rw.rewrite("CCASS是什么？")
        assert result.was_rewritten is False
        assert result.rewritten == "CCASS是什么？"
        assert result.rewrite_method == "passthrough"

    def test_term_map_not_found_graceful(self):
        """术语文件不存在时静默降级"""
        rw = QueryRewriter(term_map_path="./nonexistent.json")
        assert rw._term_map == {}

    def test_term_map_load_from_file(self):
        """从文件加载术语映射"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"mappings": {"中登公司": "中国证券登记结算有限责任公司(CSDC)"}}, f)
            tmp_path = f.name
        try:
            rw = QueryRewriter(term_map_path=tmp_path)
            assert "中登公司" in rw._term_map
            assert rw._term_map["中登公司"] == "中国证券登记结算有限责任公司(CSDC)"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_enabled_by_default(self):
        rw = QueryRewriter()
        assert rw.enabled is True

    def test_custom_timeout(self):
        rw = QueryRewriter(timeout_seconds=5.0)
        assert rw.timeout_seconds == 5.0


# ─── 术语归一化 ────────────────────────────────────────


class TestNormalizeTerms:
    def test_no_terms(self):
        """无术语映射表时透传"""
        rw = QueryRewriter(enabled=False)
        # 即使禁用了，_normalize_terms 仍独立工作
        result = rw._normalize_terms("上交所清算规则")
        assert result == "上交所清算规则"

    def test_single_replacement(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"mappings": {"中登公司": "中国证券登记结算有限责任公司(CSDC)"}}, f)
            tmp_path = f.name
        try:
            rw = QueryRewriter(term_map_path=tmp_path)
            result = rw._normalize_terms("中登公司清算规则")
            assert "中国证券登记结算有限责任公司(CSDC)" in result
            assert "中登公司" not in result
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_multiple_replacements(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"mappings": {"上交所": "上海证券交易所", "深交所": "深圳证券交易所"}}, f)
            tmp_path = f.name
        try:
            rw = QueryRewriter(term_map_path=tmp_path)
            result = rw._normalize_terms("上交所和深交所区别")
            assert "上海证券交易所" in result
            assert "深圳证券交易所" in result
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_long_term_priority(self):
        """长词优先匹配（如'中登公司上海分公司'应在'中登公司'之前）"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(
                {
                    "mappings": {
                        "中登公司": "CSDC",
                        "中登公司上海分公司": "CSDC上海",
                    }
                },
                f,
            )
            tmp_path = f.name
        try:
            rw = QueryRewriter(term_map_path=tmp_path)
            result = rw._normalize_terms("中登公司上海分公司清算规则")
            assert "CSDC上海" in result
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_no_change_for_unmatched(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"mappings": {"中登公司": "CSDC"}}, f)
            tmp_path = f.name
        try:
            rw = QueryRewriter(term_map_path=tmp_path)
            result = rw._normalize_terms("上交所清算规则")
            assert result == "上交所清算规则"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_empty_text(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"mappings": {"中登公司": "CSDC"}}, f)
            tmp_path = f.name
        try:
            rw = QueryRewriter(term_map_path=tmp_path)
            result = rw._normalize_terms("")
            assert result == ""
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ─── 多意图判断 ────────────────────────────────────────


class TestShouldDecompose:
    def test_simple_question_no_decompose(self):
        """无连词时不分解"""
        rw = QueryRewriter(enabled=False)
        assert rw._should_decompose("什么是CCASS？") is False

    def test_conjunction_triggers_decompose(self):
        """含连词时触发分解"""
        rw = QueryRewriter(enabled=False)
        assert rw._should_decompose("上交所和深交所的区别") is True

    def test_multiple_question_marks_decompose(self):
        """多个问号触发分解"""
        rw = QueryRewriter(enabled=False)
        assert rw._should_decompose("怎么登记？怎么结算？") is True

    def test_compound_term_skips_decompose(self):
        """专有术语含连词时不分解"""
        rw = QueryRewriter(enabled=False)
        assert rw._should_decompose("清算和交收系统的运行规则") is False

    def test_compound_term_with_extra_conjunction_decomposes(self):
        """专有术语外还有独立连词时仍分解"""
        rw = QueryRewriter(enabled=False)
        assert rw._should_decompose("清算和交收系统与登记系统的区别") is True

    def test_empty_text(self):
        rw = QueryRewriter(enabled=False)
        assert rw._should_decompose("") is False

    def test_single_question_mark_not_decompose(self):
        rw = QueryRewriter(enabled=False)
        assert rw._should_decompose("什么是清算？") is False


# ─── LLM 响应解析 ──────────────────────────────────────


class TestParseLlmResponse:
    def test_json_array(self):
        rw = QueryRewriter(enabled=False)
        content = """["CSDC 登记流程", "CSDC 结算流程"]"""
        result = rw._parse_llm_response(content)
        assert len(result) == 2
        assert "CSDC 登记流程" in result

    def test_json_in_code_block(self):
        rw = QueryRewriter(enabled=False)
        content = '```json\n["问题1", "问题2"]\n```'
        result = rw._parse_llm_response(content)
        assert len(result) == 2

    def test_single_item_json(self):
        rw = QueryRewriter(enabled=False)
        content = '["单一问题"]'
        result = rw._parse_llm_response(content)
        assert len(result) == 1

    def test_newline_separated(self):
        """换行分隔格式"""
        rw = QueryRewriter(enabled=False)
        content = "子问题1\n子问题2\n子问题3"
        result = rw._parse_llm_response(content)
        assert len(result) == 3

    def test_numbered_list(self):
        """有序列表格式"""
        rw = QueryRewriter(enabled=False)
        content = "1. 登记流程\n2. 结算流程"
        result = rw._parse_llm_response(content)
        assert len(result) >= 2

    def test_empty_content(self):
        rw = QueryRewriter(enabled=False)
        assert rw._parse_llm_response("") == []

    def test_invalid_json(self):
        rw = QueryRewriter(enabled=False)
        content = "这不是JSON也不是列表"
        # 应该回退到换行分隔
        result = rw._parse_llm_response(content)
        assert len(result) == 1


# ─── rewrite 主流程 ────────────────────────────────────


class TestRewrite:
    def test_passthrough_short_question(self):
        """清晰单一问题透传"""
        rw = QueryRewriter(enabled=False)
        result = rw.rewrite("什么是CCASS？")
        assert result.was_rewritten is False
        assert result.rewrite_method == "passthrough"

    def test_term_normalize(self):
        """术语归一化"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"mappings": {"中登公司": "中国证券登记结算有限责任公司(CSDC)"}}, f)
            tmp_path = f.name
        try:
            # enabled=False 只禁用 LLM 分解，术语归一化仍工作
            # 但 enabled=False 使 rewrite 立即返回 passthrough
            # 所以需要启用 rewriter 来测试术语归一化
            rw2 = QueryRewriter(term_map_path=tmp_path, enabled=True)
            # 模拟 LLM 不可用（无 API key 时跳过分解）
            result = rw2.rewrite("中登公司清算规则")
            # 无 API key → LLM 跳过 → term_normalize
            assert "中登公司" not in result.rewritten
            assert "中国证券登记结算有限责任公司(CSDC)" in result.rewritten
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_disabled_passthrough(self):
        """禁用时原始问题透传"""
        rw = QueryRewriter(enabled=False)
        result = rw.rewrite("任何问题")
        assert result.rewritten == "任何问题"
        assert result.was_rewritten is False

    def test_empty_question(self):
        rw = QueryRewriter()
        result = rw.rewrite("")
        assert result.was_rewritten is False
        assert result.rewrite_method == "passthrough"

    def test_blank_question(self):
        rw = QueryRewriter()
        result = rw.rewrite("  ")
        assert result.was_rewritten is False

    def test_get_term_map_returns_copy(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"mappings": {"A": "B"}}, f)
            tmp_path = f.name
        try:
            rw = QueryRewriter(term_map_path=tmp_path)
            m = rw.get_term_map()
            assert m["A"] == "B"
            # 返回的是副本，修改不影响内部
            m["C"] = "D"
            assert "C" not in rw._term_map
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ─── 工厂函数 ──────────────────────────────────────────


class TestCreateQueryRewriter:
    def test_create_default(self):
        rw = create_query_rewriter()
        assert isinstance(rw, QueryRewriter)
        assert rw.enabled is True

    def test_create_with_params(self):
        rw = create_query_rewriter(
            term_map_path="/custom/path.json",
            model="gpt-4o",
            timeout_seconds=10.0,
            enabled=False,
        )
        assert rw.term_map_path == "/custom/path.json"
        assert rw.model == "gpt-4o"
        assert rw.timeout_seconds == 10.0
        assert rw.enabled is False
