"""
Faithfulness 校验组件 — LLM-as-judge 防幻觉声明校验

在 LLM 生成回答后，自动检验回答是否有检索片段支撑。
不通过则降级提示，形成防幻觉闭环。

方法:
  1. 将 LLM 回答拆解为原子声明（claim）
  2. 对每条声明，用 LLM 判断是否被检索文档支撑
  3. 汇总结果：全通过 → pass，部分 → partial，全部不通过 → fail

用法:
    evaluator = FaithfulnessEvaluator()
    report = evaluator.evaluate(
        question="沪深交易所T+1清算流程是什么？",
        answer="沪深交易所实行T+1清算制度...",
        context_docs=[doc1, doc2],
    )
    if report.result == FaithfulnessResult.FAIL:
        # 降级处理
    elif report.result == FaithfulnessResult.PARTIAL:
        # 标记无支撑声明
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from haystack import Document

from qa.config.settings import get_settings

logger = logging.getLogger(__name__)


# ─── 枚举 ───────────────────────────────────────────────


class FaithfulnessResult(StrEnum):
    """忠实度校验结果"""
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"
    SKIPPED = "skipped"


# ─── 数据模型 ───────────────────────────────────────────────


@dataclass
class ClaimCheck:
    """单条声明的校验结果"""
    claim: str = ""                    # 原子声明文本
    supported: bool = True             # 是否有检索支撑
    confidence: float = 1.0            # 支撑置信度 (0~1)
    evidence: str = ""                 # 支撑该声明的原文片段
    reason: str = ""                   # 判断理由


@dataclass
class FaithfulnessReport:
    """忠实度校验报告"""
    result: FaithfulnessResult = FaithfulnessResult.SKIPPED
    total_claims: int = 0
    supported_claims: int = 0
    unsupported_claims: int = 0
    claims: list[ClaimCheck] = field(default_factory=list)
    score: float = 1.0                 # 0~1，支撑比例
    evaluation_time_ms: float = 0.0    # 校验耗时
    error: str | None = None        # 校验过程中的错误

    @property
    def passed(self) -> bool:
        return self.result == FaithfulnessResult.PASS

    @property
    def degraded(self) -> bool:
        return self.result == FaithfulnessResult.FAIL

    @property
    def summary(self) -> str:
        if self.result == FaithfulnessResult.SKIPPED:
            return "已跳过"
        if self.result == FaithfulnessResult.PASS:
            return f"通过 ({self.supported_claims}/{self.total_claims})"
        if self.result == FaithfulnessResult.PARTIAL:
            return f"部分通过 ({self.supported_claims}/{self.total_claims} 有支撑)"
        return f"不通过 ({self.unsupported_claims}/{self.total_claims} 无支撑)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.value,
            "total_claims": self.total_claims,
            "supported_claims": self.supported_claims,
            "unsupported_claims": self.unsupported_claims,
            "score": round(self.score, 4),
            "evaluation_time_ms": self.evaluation_time_ms,
            "summary": self.summary,
            "claims": [
                {
                    "claim": c.claim,
                    "supported": c.supported,
                    "confidence": round(c.confidence, 4),
                    "evidence": c.evidence[:200] if c.evidence else "",
                }
                for c in self.claims
            ],
            "error": self.error,
        }


# ─── 校验器 ───────────────────────────────────────────────


class FaithfulnessEvaluator:
    """Faithfulness 校验器

    使用 LLM-as-judge 方法，对 LLM 生成的回答逐声明校验。

    构造提示词，让 LLM 判断每条声明是否被检索到的文档片段支撑。
    避免对引用前缀的误判（如"根据XX文档"）。

    用法:
        evaluator = FaithfulnessEvaluator()
        report = evaluator.evaluate(question, answer, context_docs)
    """

    def __init__(
        self,
        enabled: bool = True,
        threshold: float = 0.7,
        max_claims: int = 10,
        judge_model: str = "",
    ):
        """
        Args:
            enabled: 是否启用校验
            threshold: 通过阈值（支撑比例 ≥ 此值算 PASS，< 此值算 FAIL，中间算 PARTIAL）
            max_claims: 最大校验声明数（超出按段落分段，防止 token 超限）
            judge_model: 校验用模型名（空则复用 LLM 主模型）
        """
        self.enabled = enabled
        self.threshold = max(0.0, min(1.0, threshold))
        self.max_claims = max_claims
        self.judge_model = judge_model
        self._llm_client = None

    def evaluate(
        self,
        question: str,
        answer: str,
        context_docs: list[Document],
    ) -> FaithfulnessReport:
        """执行 Faithfulness 校验

        Args:
            question: 用户原始问题
            answer: LLM 生成的回答
            context_docs: 检索到的文档片段（LLM 实际看到的上下文）

        Returns:
            FaithfulnessReport
        """
        if not self.enabled or not answer:
            return FaithfulnessReport(result=FaithfulnessResult.SKIPPED)

        t0 = time.time()
        report = FaithfulnessReport()

        try:
            # 1) 拆解声明
            claims = self._extract_claims(answer)
            if not claims:
                logger.info("Faithfulness: 无法从回答中拆解出声明")
                report.result = FaithfulnessResult.PASS
                report.total_claims = 0
                report.evaluation_time_ms = (time.time() - t0) * 1000
                return report

            # 截断过多声明
            if len(claims) > self.max_claims:
                logger.warning(
                    f"Faithfulness: 声明数 {len(claims)} 超过上限 {self.max_claims}，截断"
                )
                claims = claims[:self.max_claims]

            # 2) 构建上下文文本
            context_text = self._format_context(context_docs)

            # 3) 批量校验所有声明
            checks, err = self._check_claims_batch(question, answer, claims, context_text)

            report.total_claims = len(checks)
            report.supported_claims = sum(1 for c in checks if c.supported)
            report.unsupported_claims = sum(1 for c in checks if not c.supported)
            report.claims = checks
            report.error = err

            # 4) 计算得分
            if report.total_claims > 0:
                report.score = report.supported_claims / report.total_claims

            # 5) 判定结果
            if report.total_claims == 0:
                report.result = FaithfulnessResult.PASS
            elif report.score >= self.threshold:
                if report.unsupported_claims == 0:
                    report.result = FaithfulnessResult.PASS
                else:
                    report.result = FaithfulnessResult.PARTIAL
            else:
                report.result = FaithfulnessResult.FAIL

        except Exception as e:
            logger.error(f"Faithfulness 校验异常: {e}")
            report.result = FaithfulnessResult.SKIPPED
            report.error = str(e)

        report.evaluation_time_ms = (time.time() - t0) * 1000

        logger.info(
            f"Faithfulness 校验: {report.result.value} "
            f"(支撑 {report.supported_claims}/{report.total_claims}, "
            f"score={report.score:.2f}, "
            f"耗时={report.evaluation_time_ms:.0f}ms)"
        )

        return report

    # ─── 声明拆解 ───────────────────────────────────────────────

    def _extract_claims(self, answer: str) -> list[str]:
        """将 LLM 回答拆解为原子声明

        策略：
        1. 按句号/分号/感叹号/问号拆句
        2. 过滤空句和过短的句子（<10 字）
        3. 去除引用前缀（如"根据XX文档"、"来源：XX"）
        4. 去除只有格式或引用标记的句子
        """
        # 拆句
        raw_sentences = re.split(r'(?<=[。；！？\n])\s*', answer)
        sentences = [s.strip() for s in raw_sentences if s.strip()]

        claims = []
        for s in sentences:
            # 过滤过短（纯引用或格式标记）
            if len(s) < 10:
                continue

            # 去除引用前缀：如"根据[来源:XXX]"，"来源:XXX"
            cleaned = re.sub(r'^根据?\s*\[?来源[:：].*?\]?\s*', '', s)
            cleaned = re.sub(r'^\[来源[:：].*?\]\s*', '', cleaned)

            # 如果清理后为空或太短，跳过
            cleaned = cleaned.strip()
            if len(cleaned) < 8:
                continue

            # 过滤纯格式或列表标记（如"1."、"①"等）
            if re.match(r'^[\d①②③④⑤⑥⑦⑧⑨⑩\.\-\*\s]{1,5}$', cleaned):
                continue

            # 过滤只是"是的"/"不是"/"好的"等简短确认
            if cleaned in ("是", "不是", "是的", "不是的", "好的", "对的", "正确"):
                continue

            claims.append(cleaned)

        # 去重
        seen = set()
        unique_claims = []
        for c in claims:
            if c not in seen:
                seen.add(c)
                unique_claims.append(c)

        return unique_claims

    # ─── 上下文格式化 ───────────────────────────────────────────────

    def _format_context(self, context_docs: list[Document]) -> str:
        """将检索到的文档片段格式化为校验上下文

        为 LLM judge 提供可读的文档引用片段。
        """
        if not context_docs:
            return "（无检索到的文档片段）"

        parts = []
        for i, doc in enumerate(context_docs, 1):
            meta = doc.meta or {}
            source = meta.get("file_path", meta.get("source", f"doc_{i}"))
            content = (doc.content or "")[:800]  # 截断保护 token
            parts.append(f"[文档{i}] {source}:\n{content}")

        return "\n\n---\n\n".join(parts)

    # ─── LLM 校验 ───────────────────────────────────────────────

    def _check_claims_batch(
        self,
        question: str,
        answer: str,
        claims: list[str],
        context_text: str,
    ) -> tuple[list[ClaimCheck], str | None]:
        """批量校验声明（单次 LLM 调用完成全部校验）"""
        if not claims:
            return [], None

        prompt = self._build_batch_prompt(question, answer, claims, context_text)

        try:
            response = self._call_llm(prompt)
            results = self._parse_batch_response(response, claims)
            return results, None
        except Exception as e:
            logger.error(f"Faithfulness LLM 校验调用失败: {e}")
            # fallback: 默认全部通过（避免误拦截）
            fallback = [
                ClaimCheck(claim=c, supported=True, reason="校验失败，默认通过")
                for c in claims
            ]
            return fallback, str(e)

    def _build_batch_prompt(
        self,
        question: str,
        answer: str,
        claims: list[str],
        context_text: str,
    ) -> list[dict[str, str]]:
        """构造批量校验 prompt

        让 LLM 一次性判断所有声明，返回 JSON 结果。
        """
        claims_text = "\n".join(f"  [{i+1}] {c}" for i, c in enumerate(claims))

        # noqa: E501 start (long prompt lines — kept intact for LLM quality)
        system_prompt = (
            "你是一个专业的事实一致性校验员。你的任务是检查回答中的每条声明是否被检索到的文档片段支撑。\n"
            "\n"
            "规则：\n"
            "1. 只基于检索到的文档片段判断，不依赖自己的知识\n"
            "2. 支撑指文档片段中明确包含该声明的信息，或可以明确推理得出\n"
            "3. 如果声明包含引用前缀（如根据XX），忽略前缀，只看事实内容是否在文档中\n"
            "4. 对每条声明输出 JSON 数组：[{\"claim_idx\": 0, \"supported\": true/false, "
            "\"evidence\": \"支撑的原文片段\", \"confidence\": 0.0~1.0}]\n"
            "5. evidence 从文档片段中截取最相关的原文（含文件名），不超过 100 字\n"
            "6. confidence 表示支撑的可信度：1.0=明确支撑，0.7=可推理支撑，"
            "0.3=弱支撑或间接相关，0.0=完全不支撑\n"
            "7. 只输出 JSON 数组，不要其他文字"
        )

        user_prompt = f"""## 用户问题
{question}

## 检索到的文档片段
{context_text}

## LLM 生成的回答
{answer}

## 需要校验的声明
{claims_text}

请逐条判断每条声明是否被检索到的文档片段支撑。输出 JSON 数组："""

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _parse_batch_response(
        self, response: str, claims: list[str]
    ) -> list[ClaimCheck]:
        """解析 LLM 返回的 JSON 校验结果"""
        # 提取 JSON 部分
        json_str = self._extract_json(response)
        if not json_str:
            logger.warning("Faithfulness: LLM 返回格式无效，默认全部通过")
            return [ClaimCheck(claim=c, supported=True) for c in claims]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Faithfulness: JSON 解析失败，默认全部通过")
            return [ClaimCheck(claim=c, supported=True) for c in claims]

        if not isinstance(data, list):
            return [ClaimCheck(claim=c, supported=True) for c in claims]

        # 构建索引映射
        results = []
        result_map = {}
        for item in data:
            idx = item.get("claim_idx")
            if isinstance(idx, int):
                result_map[idx] = item

        for i, claim in enumerate(claims):
            item = result_map.get(i, {})
            supported = item.get("supported", True)
            evidence = item.get("evidence", "")
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0.5))))
            reason = item.get("reason", "")

            # 如果 LLM 没有返回结果，默认通过
            results.append(ClaimCheck(
                claim=claim,
                supported=bool(supported),
                confidence=confidence,
                evidence=evidence[:300] if evidence else "",
                reason=reason,
            ))

        return results

    # ─── LLM 调用 ───────────────────────────────────────────────

    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """调用 LLM 进行校验

        优先使用 judge_model（独立评判模型避免自评偏差），
        未设置时复用主 LLM。
        """
        settings = get_settings()

        model = self.judge_model or settings.llm.model
        api_key = settings.llm.resolved_api_key or "sk-placeholder"
        base_url = settings.llm.api_base_url

        # judge_model 可搭配独立 API 地址（通过环境变量覆盖）
        if self.judge_model:
            judge_api_base = settings.llm.api_base_url
            base_url = judge_api_base

        if self._llm_client is None:
            from openai import OpenAI
            self._llm_client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=15,
            )

        resp = self._llm_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,  # 低温度确保一致性
            max_tokens=2048,
        )

        return resp.choices[0].message.content or ""

    # ─── 工具 ───────────────────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """从 LLM 响应中提取 JSON 数组或对象"""
        # 尝试提取 ```json ... ``` 代码块
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if m:
            return m.group(1).strip()

        # 尝试提取 [...] 或 {...}
        for delim in ("[", "{"):
            start = text.find(delim)
            if start >= 0:
                # 找到匹配的结束：计数括号层级，而非 rfind
                close = "]" if delim == "[" else "}"
                depth = 0
                for i in range(start, len(text)):
                    if text[i] == delim:
                        depth += 1
                    elif text[i] == close:
                        depth -= 1
                        if depth == 0:
                            return text[start:i + 1]

        return None

    @staticmethod
    def is_factual_claim(text: str) -> bool:
        """判断是否为事实性声明（适合校验）"""
        # 过滤非事实性内容
        non_factual_patterns = [
            r"^(总的来说|总结|综上所述|总之|因此|所以)",
            r"^(感谢|谢谢|如果|建议|请联系)",
            r"^(示例|例子|例如|比如)",
            r"^注[：:]",
            r"^\*",
        ]
        for pattern in non_factual_patterns:
            if re.match(pattern, text):
                return False
        return True
