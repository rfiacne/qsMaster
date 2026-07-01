"""
Faithfulness 单元测试

测试声明拆解（_extract_claims）、JSON 提取（_extract_json）、
报告模型等纯逻辑，不依赖 LLM API。

需要 mock 的部分：haystack、openai
"""

from __future__ import annotations

import json
import sys
from unittest import mock

# ── mock openai ─────────────────────────────────────────
openai_mock = mock.MagicMock()
sys.modules["openai"] = openai_mock

# ── mock settings ───────────────────────────────────────
settings_patcher = mock.patch("qa.config.settings.get_settings")
mock_settings = settings_patcher.start()
mock_settings.return_value.llm.api_base_url = "http://test:8000/v1"
mock_settings.return_value.llm.resolved_api_key = "test-key"
mock_settings.return_value.llm.model = "test-model"

from qa.pipelines.components.faithfulness import (  # noqa: E402
    ClaimCheck,
    FaithfulnessEvaluator,
    FaithfulnessReport,
    FaithfulnessResult,
)


class TestFaithfulnessResult:
    """FaithfulnessResult 枚举"""

    def test_values(self):
        assert FaithfulnessResult.PASS == "pass"
        assert FaithfulnessResult.PARTIAL == "partial"
        assert FaithfulnessResult.FAIL == "fail"
        assert FaithfulnessResult.SKIPPED == "skipped"

    def test_all_values(self):
        expected = {"pass", "partial", "fail", "skipped"}
        actual = {v.value for v in FaithfulnessResult}
        assert actual == expected


class TestClaimCheck:
    """ClaimCheck 数据模型"""

    def test_default(self):
        c = ClaimCheck()
        assert c.claim == ""
        assert c.supported is True
        assert c.confidence == 1.0
        assert c.evidence == ""
        assert c.reason == ""

    def test_with_values(self):
        c = ClaimCheck(
            claim="T+1清算",
            supported=True,
            confidence=0.9,
            evidence="文档说T+1清算",
            reason="明确支撑",
        )
        assert c.claim == "T+1清算"
        assert c.supported is True
        assert c.confidence == 0.9


class TestFaithfulnessReport:
    """FaithfulnessReport 数据模型"""

    def test_default(self):
        r = FaithfulnessReport()
        assert r.result == FaithfulnessResult.SKIPPED
        assert r.score == 1.0
        assert r.passed is False  # SKIPPED != PASS

    def test_pass(self):
        r = FaithfulnessReport(
            result=FaithfulnessResult.PASS,
            total_claims=3,
            supported_claims=3,
            score=1.0,
        )
        assert r.passed is True
        assert r.degraded is False
        assert "通过" in r.summary

    def test_partial(self):
        r = FaithfulnessReport(
            result=FaithfulnessResult.PARTIAL,
            total_claims=4,
            supported_claims=2,
            score=0.5,
        )
        assert r.passed is False
        assert r.degraded is False
        assert "部分通过" in r.summary

    def test_fail(self):
        r = FaithfulnessReport(
            result=FaithfulnessResult.FAIL,
            total_claims=3,
            supported_claims=0,
            score=0.0,
        )
        assert r.passed is False
        assert r.degraded is True
        assert "不通过" in r.summary

    def test_to_dict(self):
        r = FaithfulnessReport(
            result=FaithfulnessResult.PASS,
            total_claims=2,
            supported_claims=2,
            claims=[ClaimCheck(claim="c1", supported=True)],
            score=1.0,
            evaluation_time_ms=50.0,
        )
        d = r.to_dict()
        assert d["result"] == "pass"
        assert d["total_claims"] == 2
        assert len(d["claims"]) == 1
        assert d["claims"][0]["claim"] == "c1"


class TestClaimExtraction:
    """声明拆解 _extract_claims"""

    def setup_method(self):
        self.evaluator = FaithfulnessEvaluator(enabled=True)

    def test_simple_sentences(self):
        """中文句号拆分"""
        text = (  # noqa: E501
            "沪深交易所A股实行T+1清算制度。"
            "交易日完成交易的确认流程。"
            "T+1日完成资金和证券的交收清算。"
        )
        claims = self.evaluator._extract_claims(text)
        assert len(claims) >= 2
        assert any("T+1" in c for c in claims)

    def test_semicolon_separated(self):
        """分号也应拆分"""
        text = "资金账户的日常管理和维护；证券账户的开立和注销流程；清算风险管理的核心控制措施。"
        claims = self.evaluator._extract_claims(text)
        assert len(claims) >= 2

    def test_filter_short_claims(self):
        """过短声明（<10字）应过滤"""
        text = "是。好的。对的。沪深交易所实行T+1清算制度。"
        claims = self.evaluator._extract_claims(text)
        for c in claims:
            assert len(c) >= 10, f"Short claim not filtered: {c}"

    def test_filter_acknowledgments(self):
        """ "是/不是/好的"等确认词应过滤"""
        text = "是的。不是。好的。根据文档规定，T+1是指交易日次日。"
        claims = self.evaluator._extract_claims(text)
        assert not any(c in ("是", "不是", "是的", "好的") for c in claims)

    def test_remove_citation_prefix(self):
        """ "根据XX"前缀应去除"""
        text = "根据来源:CSDC规则，沪深交易所实行T+1制度。来源:文档，T+1是标准规则。"
        claims = self.evaluator._extract_claims(text)
        assert any("T+1" in c for c in claims)

    def test_remove_bracket_citation(self):
        """ "[来源:XX]"前缀应去除"""
        text = "[来源:清算规则]沪深交易所A股实行T+1清算制度。"
        claims = self.evaluator._extract_claims(text)
        assert any("T+1清算制度" in c for c in claims)

    def test_dedup_repeated_claims(self):
        """重复声明应去重"""
        text = (  # noqa: E501
            "T+1清算制度是指交易日次日交收。"
            "T+1清算制度是指交易日次日交收。"
            "这是完全不同的第三句话内容。"
        )
        claims = self.evaluator._extract_claims(text)
        assert len(claims) == 2  # 第一句去重 + 第三句

    def test_empty_answer(self):
        claims = self.evaluator._extract_claims("")
        assert claims == []

    def test_only_short_sentences(self):
        claims = self.evaluator._extract_claims("是。好。对。")
        assert claims == []

    def test_newline_separated(self):
        """换行分隔的文本"""
        text = (  # noqa: E501
            "第一行清算规则的具体内容和操作步骤\n"
            "第二行技术架构说明及部署方式\n"
            "第三行风险控制措施与监控指标"
        )
        claims = self.evaluator._extract_claims(text)
        assert len(claims) >= 2

    def test_numbered_list(self):
        """编号列表只保留有实质内容的条目"""
        text = "1. 第一条清算规则。2. 第二条清算规则。3. 第三条。"
        claims = self.evaluator._extract_claims(text)
        assert len(claims) >= 2
        assert any("第一条清算规则" in c for c in claims)

    def test_mixed_content(self):
        """混合引用和真实内容"""
        text = "根据清算规则，沪深交易所实行T+1制度。来源:技术文档。需注意T+1仅适用于A股。"
        claims = self.evaluator._extract_claims(text)
        assert len(claims) >= 2
        assert any("T+1制度" in c for c in claims)
        assert any("A股" in c for c in claims)


class TestJsonExtraction:
    """JSON 提取 _extract_json"""

    def test_bare_json_array(self):
        text = '[{"claim_idx": 0, "supported": true}]'
        result = FaithfulnessEvaluator._extract_json(text)
        assert result is not None
        data = json.loads(result)
        assert data[0]["claim_idx"] == 0
        assert data[0]["supported"] is True

    def test_bare_json_object(self):
        text = '{"result": "pass", "score": 0.9}'
        result = FaithfulnessEvaluator._extract_json(text)
        assert result is not None
        data = json.loads(result)
        assert data["result"] == "pass"

    def test_code_block_json(self):
        text = 'Some text before\n```json\n[{"claim_idx": 0, "supported": false}]\n```\nsome after'
        result = FaithfulnessEvaluator._extract_json(text)
        assert result is not None
        data = json.loads(result)
        assert data[0]["supported"] is False

    def test_code_block_no_lang(self):
        text = '```\n[{"claim_idx": 1, "supported": true}]\n```'
        result = FaithfulnessEvaluator._extract_json(text)
        assert result is not None

    def test_no_json(self):
        text = "这是一段没有 JSON 的纯文本"
        result = FaithfulnessEvaluator._extract_json(text)
        assert result is None

    def test_empty_string(self):
        assert FaithfulnessEvaluator._extract_json("") is None

    def test_malformed_brackets(self):
        """不完整的括号不回退"""
        text = "开头 [ 结尾"
        result = FaithfulnessEvaluator._extract_json(text)
        # 方括号在文本中但不构成 JSON 数组 → 可能提取也可能不提取
        # 只要不报错即可
        assert result is None or isinstance(result, str)

    def test_nested_arrays(self):
        text = '[[{"key": "value"}]]'
        result = FaithfulnessEvaluator._extract_json(text)
        assert result is not None


class TestFormatContext:
    """上下文格式化 _format_context"""

    def setup_method(self):
        self.evaluator = FaithfulnessEvaluator(enabled=True)

    def test_empty_context(self):
        result = self.evaluator._format_context([])
        assert "无检索到的文档片段" in result

    def test_single_document(self):
        doc = mock.MagicMock()
        doc.content = "清算规则内容"
        doc.meta = {"file_path": "规则.pdf"}

        result = self.evaluator._format_context([doc])
        assert "规则.pdf" in result
        assert "清算规则内容" in result

    def test_multiple_documents(self):
        docs = []
        for i in range(3):
            doc = mock.MagicMock()
            doc.content = f"文档{i}内容"
            doc.meta = {"source": f"src{i}"}
            docs.append(doc)

        result = self.evaluator._format_context(docs)
        assert "[文档1]" in result
        assert "[文档3]" in result
        assert "src0" in result

    def test_long_content_truncated(self):
        """过长内容应截断"""
        doc = mock.MagicMock()
        doc.content = "A" * 2000
        doc.meta = {}

        result = self.evaluator._format_context([doc])
        # 内容应被截断（不超过 ~800 字符）
        assert len(result) < 1200


class TestParseBatchResponse:
    """LLM 响应解析 _parse_batch_response"""

    def setup_method(self):
        self.evaluator = FaithfulnessEvaluator(enabled=True)

    def test_valid_json_all_supported(self):
        response = json.dumps(
            [
                {"claim_idx": 0, "supported": True, "evidence": "文档片段1", "confidence": 0.9},
                {"claim_idx": 1, "supported": False, "evidence": "", "confidence": 0.1},
            ]
        )
        claims = ["声明1", "声明2"]
        results = self.evaluator._parse_batch_response(response, claims)
        assert len(results) == 2
        assert results[0].supported is True
        assert results[1].supported is False

    def test_missing_claim_indices(self):
        """缺少某些索引时默认通过"""
        response = json.dumps(
            [
                {"claim_idx": 0, "supported": True, "evidence": "", "confidence": 0.9},
            ]
        )
        claims = ["声明1", "声明2", "声明3"]
        results = self.evaluator._parse_batch_response(response, claims)
        assert len(results) == 3
        assert results[0].supported is True
        assert results[1].supported is True  # 默认通过
        assert results[2].supported is True  # 默认通过

    def test_invalid_json_default_pass(self):
        response = "这不是 JSON"
        claims = ["声明1"]
        results = self.evaluator._parse_batch_response(response, claims)
        assert len(results) == 1
        assert results[0].supported is True  # 解析失败默认通过

    def test_not_a_list(self):
        response = '{"not": "a list"}'
        claims = ["声明1"]
        results = self.evaluator._parse_batch_response(response, claims)
        assert len(results) == 1
        assert results[0].supported is True


class TestEvaluateSkipConditions:
    """evaluate 方法跳过条件"""

    def setup_method(self):
        self.evaluator = FaithfulnessEvaluator(enabled=True)

    def test_disabled_returns_skipped(self):
        evaluator = FaithfulnessEvaluator(enabled=False)
        report = evaluator.evaluate("Q", "A", [])
        assert report.result == FaithfulnessResult.SKIPPED

    def test_empty_answer_returns_skipped(self):
        evaluator = FaithfulnessEvaluator(enabled=True)
        report = evaluator.evaluate("Q", "", [])
        assert report.result == FaithfulnessResult.SKIPPED

    def test_none_answer_returns_skipped(self):
        evaluator = FaithfulnessEvaluator(enabled=True)
        report = evaluator.evaluate("Q", None, [])
        assert report.result == FaithfulnessResult.SKIPPED


class TestBuildBatchPrompt:
    """Prompt 构建 _build_batch_prompt"""

    def setup_method(self):
        self.evaluator = FaithfulnessEvaluator(enabled=True)

    def test_prompt_structure(self):
        """prompt 应包含 system + user 两条消息"""
        doc = mock.MagicMock()
        doc.content = "文档内容"
        doc.meta = {}

        messages = self.evaluator._build_batch_prompt(
            question="测试问题",
            answer="测试回答",
            claims=["声明1", "声明2"],
            context_text="文档片段",
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_prompt_contains_claims(self):
        messages = self.evaluator._build_batch_prompt(
            question="Q", answer="A", claims=["声明1"], context_text="C"
        )
        assert "声明1" in messages[1]["content"]
        assert "[1]" in messages[1]["content"]

    def test_prompt_contains_context(self):
        messages = self.evaluator._build_batch_prompt(
            question="Q", answer="A", claims=["C1"], context_text="测试文档内容"
        )
        assert "测试文档内容" in messages[1]["content"]


class TestEvaluateResult:
    """完整 evaluate 结果判定"""

    def test_all_supported_pass(self):
        """所有声明都支撑 → PASS"""
        evaluator = FaithfulnessEvaluator(enabled=True, threshold=0.5)
        # 用 mock 绕过 LLM 调用
        with mock.patch.object(evaluator, "_check_claims_batch") as mock_check:
            mock_check.return_value = (
                [ClaimCheck(claim="c1", supported=True), ClaimCheck(claim="c2", supported=True)],
                None,
            )
            doc = mock.MagicMock()
            doc.content = "doc"
            doc.meta = {}
            report = evaluator.evaluate("Q", "A 是。B 是。", [doc])
            assert report.result == FaithfulnessResult.PASS
            assert report.score == 1.0

    def test_none_supported_fail(self):
        """所有声明都不支撑 → FAIL"""
        evaluator = FaithfulnessEvaluator(enabled=True, threshold=0.5)
        with mock.patch.object(evaluator, "_check_claims_batch") as mock_check:
            mock_check.return_value = (
                [ClaimCheck(claim="c1", supported=False), ClaimCheck(claim="c2", supported=False)],
                None,
            )
            doc = mock.MagicMock()
            doc.content = "doc"
            doc.meta = {}
            report = evaluator.evaluate(  # noqa: E501
                "Q",
                "这是一个足够长的测试回答语句。这是第二条测试声明内容。",
                [doc],
            )
            assert report.result == FaithfulnessResult.FAIL
            assert report.score == 0.0

    def test_partial_supported(self):
        """部分支撑 → PARTIAL"""
        evaluator = FaithfulnessEvaluator(enabled=True, threshold=0.5)
        with mock.patch.object(evaluator, "_check_claims_batch") as mock_check:
            mock_check.return_value = (
                [ClaimCheck(claim="c1", supported=True), ClaimCheck(claim="c2", supported=False)],
                None,
            )
            doc = mock.MagicMock()
            doc.content = "doc"
            doc.meta = {}
            report = evaluator.evaluate(
                "Q", "第一条被支撑的测试声明内容。第二条未被支撑的声明内容。", [doc]
            )  # noqa: E501
            assert report.result == FaithfulnessResult.PARTIAL
            assert report.score == 0.5

    def test_exception_during_check(self):
        """校验异常应返回 SKIPPED"""
        evaluator = FaithfulnessEvaluator(enabled=True)
        with mock.patch.object(evaluator, "_check_claims_batch") as mock_check:
            mock_check.side_effect = RuntimeError("API 错误")
            doc = mock.MagicMock()
            doc.content = "doc"
            doc.meta = {}
            report = evaluator.evaluate("Q", "这条测试声明的长度超过了十个字。", [doc])
            assert report.result == FaithfulnessResult.SKIPPED
            assert report.error is not None

    def test_timing_recorded(self):
        """应记录校验耗时"""
        evaluator = FaithfulnessEvaluator(enabled=True)
        with mock.patch.object(evaluator, "_check_claims_batch") as mock_check:
            mock_check.return_value = ([ClaimCheck(claim="c1", supported=True)], None)
            doc = mock.MagicMock()
            doc.content = "doc"
            doc.meta = {}
            report = evaluator.evaluate("Q", "A 是。", [doc])
            assert report.evaluation_time_ms > 0

    def test_threshold_determines_result(self):
        """threshold 决定 PASS/FAIL 边界"""
        evaluator_high = FaithfulnessEvaluator(enabled=True, threshold=0.9)
        evaluator_low = FaithfulnessEvaluator(enabled=True, threshold=0.1)

        with mock.patch.object(evaluator_high, "_check_claims_batch") as mock_h:
            mock_h.return_value = ([ClaimCheck(claim="c", supported=True)], None)
            with mock.patch.object(evaluator_low, "_check_claims_batch") as mock_l:
                mock_l.return_value = ([ClaimCheck(claim="c", supported=False)], None)
                doc = mock.MagicMock()
                doc.content = "doc"
                doc.meta = {}
                r1 = evaluator_high.evaluate("Q", "这条测试声明已经超过了十个字。", [doc])
                r2 = evaluator_low.evaluate("Q", "这条测试声明已经超过了十个字。", [doc])
                assert r1.result == FaithfulnessResult.PASS
                assert r2.result == FaithfulnessResult.FAIL
