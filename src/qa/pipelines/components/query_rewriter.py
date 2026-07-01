"""
查询改写层 — 术语归一化 + 多意图分解

在嵌入检索前对用户问题进行预处理：
1. 术语归一化：证券简称→全称（如"中登公司"→"中国证券登记结算有限责任公司(CSDC)"）
2. 多意图分解：将复合问题拆分为子问题分别检索后合并

边界用例：
- 清晰单一问题 → 直接透传，不调 LLM
- 含"和/与/及/分别"的复合问题 → LLM 判定后分解
- 术语问题含"和"但为专有术语（如"清算和交收系统"） → 透传
- LLM 不可用/超时 → 回退原始问题
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 多意图连词（触发 LLM 判定的标志性词汇）
INTENT_CONJUNCTIONS = [
    "和",
    "与",
    "及",
    "以及",
    "分别",
    "同时",
]

# 证券清算常见专有术语（含"和"但不拆分的固定搭配）
COMPOUND_TERMS = [
    "清算和交收系统",
    "清算和结算",
    "交易和清算",
    "登记和托管",
    "资金和证券",
    "交易和结算",
    "清算和交收",
    "发行和上市",
]


@dataclass
class QueryRewriteResult:
    """查询改写结果"""

    original: str = ""  # 原始问题
    rewritten: str = ""  # 改写后问题（或子问题合并后的最终查询）
    sub_questions: list[str] = field(default_factory=list)  # 子问题列表
    was_rewritten: bool = False  # 是否实际改写
    rewrite_time_ms: float = 0.0  # 改写耗时
    rewrite_method: str = ""  # "passthrough" | "term_normalize" | "llm_decompose"
    error: str | None = None  # 改写过程中的错误


class QueryRewriter:
    """查询改写器

    用法:
        rewriter = QueryRewriter(term_map_path="./data/term_map.json")
        result = rewriter.rewrite("中登公司和上交所的清算流程分别是什么？")
    """

    def __init__(
        self,
        term_map_path: str = "./data/term_map.json",
        model: str = "",
        timeout_seconds: float = 3.0,
        enabled: bool = True,
    ):
        self.term_map_path = term_map_path
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled
        self._term_map: dict[str, str] = {}
        self._term_patterns: list[tuple[re.Pattern[str], str]] = []
        self._load_term_map()

    def _load_term_map(self) -> None:
        """加载术语映射表"""
        path = Path(self.term_map_path)
        if not path.exists():
            logger.info(f"术语映射表不存在: {path}，跳过术语归一化")
            return

        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            self._term_map = raw.get("mappings", raw)
            if not isinstance(self._term_map, dict):
                logger.warning(f"术语映射表格式无效: {path}")
                return

            # 预编译正则（按长度降序排序，长词优先匹配）
            sorted_terms = sorted(self._term_map.keys(), key=len, reverse=True)
            self._term_patterns = [
                (re.compile(re.escape(term)), self._term_map[term]) for term in sorted_terms
            ]
            logger.info(f"术语映射表加载完成: {len(self._term_patterns)} 条映射")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"术语映射表加载失败: {e}")

    def rewrite(self, question: str) -> QueryRewriteResult:
        """执行查询改写

        Args:
            question: 用户原始问题

        Returns:
            QueryRewriteResult
        """
        if not self.enabled or not question.strip():
            return QueryRewriteResult(
                original=question,
                rewritten=question,
                was_rewritten=False,
                rewrite_method="passthrough",
            )

        t0 = time.time()
        result = QueryRewriteResult(original=question)

        # Step 1: 术语归一化
        normalized = self._normalize_terms(question)

        # Step 2: 判断是否需要 LLM 分解
        if self._should_decompose(normalized):
            try:
                sub_questions = self._decompose_with_llm(normalized)
                if sub_questions and len(sub_questions) > 1:
                    # 成功分解多意图
                    result.sub_questions = sub_questions
                    # 用第一个子问题+补充作为改写后问题
                    result.rewritten = sub_questions[0]
                    result.was_rewritten = True
                    result.rewrite_method = "llm_decompose"
                elif sub_questions and len(sub_questions) == 1:
                    # LLM 判定为单一意图，用归一化后的文本
                    result.rewritten = sub_questions[0]
                    result.was_rewritten = normalized != question
                    result.rewrite_method = (
                        "term_normalize" if result.was_rewritten else "passthrough"
                    )
                else:
                    # 分解失败，回退到归一化文本
                    result.rewritten = normalized
                    result.rewrite_method = (
                        "term_normalize" if normalized != question else "passthrough"
                    )
            except Exception as e:
                logger.warning(f"LLM 分解失败，回退原始问题: {e}")
                result.error = str(e)
                result.rewritten = normalized
                result.rewrite_method = (
                    "term_normalize" if normalized != question else "passthrough"
                )
        else:
            # 单一意图透传
            result.rewritten = normalized
            result.was_rewritten = normalized != question
            result.rewrite_method = "term_normalize" if result.was_rewritten else "passthrough"

        result.rewrite_time_ms = (time.time() - t0) * 1000

        logger.debug(
            f"查询改写: method={result.rewrite_method}, "
            f"was_rewritten={result.was_rewritten}, "
            f"耗时={result.rewrite_time_ms:.0f}ms, "
            f"子问题数={len(result.sub_questions)}"
        )

        return result

    def _normalize_terms(self, text: str) -> str:
        """术语归一化：简称→全称

        按预编译正则替换，长词优先匹配。
        """
        if not self._term_patterns:
            return text

        result = text
        for pattern, replacement in self._term_patterns:
            result = pattern.sub(replacement, result)

        if result != text:
            logger.debug(f'术语归一化: "{text}" → "{result}"')

        return result

    def _should_decompose(self, text: str) -> bool:
        """判断是否需要尝试多意图分解

        优先级:
        1. 多个问号（?/?）→ 直接分解
        2. 含连词且不在专有术语中 → LLM 判定
        """
        # 检查问号数量 > 1（强信号：直接表示多个问题）
        if text.count("?") + text.count("？") > 1:
            return True

        # 检查是否包含连词
        has_conjunction = any(c in text for c in INTENT_CONJUNCTIONS)
        if not has_conjunction:
            return False

        # 检查文本是否完全由某个专有术语覆盖
        for term in COMPOUND_TERMS:
            if term in text:
                remaining = text
                for t in COMPOUND_TERMS:
                    remaining = remaining.replace(t, "")
                if any(c in remaining for c in INTENT_CONJUNCTIONS):
                    return True
                return False

        return True

    def _decompose_with_llm(self, text: str) -> list[str]:
        """LLM 多意图分解

        调用 LLM 判断问题是否含多意图，若是则分解为子问题列表。

        Returns:
            子问题列表。空列表表示分解失败。
        """
        from openai import OpenAI

        from qa.config.settings import get_settings

        settings = get_settings()
        model = self.model or settings.llm.model

        api_key = settings.llm.resolved_api_key
        # 未配置 API 密钥时跳过 LLM 分解（回退到归一化文本），
        # 不发送伪造的 sk-placeholder 凭据
        if not api_key:
            logger.debug("查询改写: 未配置 LLM API 密钥，跳过多意图分解")
            return []

        client = OpenAI(
            api_key=api_key,
            base_url=settings.llm.api_base_url,
            timeout=self.timeout_seconds,
        )

        system_prompt = (
            "你是一个证券清算领域的查询分析助手。你的任务是判断用户问题是否包含"
            "多个独立意图，并分解为独立的子问题。\n\n"
            "规则：\n"
            "1. 如果问题只包含单一意图（即使包含'和'、'与'等连词，但描述的是一个整体概念），"
            "只返回原始问题本身\n"
            "2. 如果问题确实包含多个独立意图（如'分别说明A和B'、'A和B各是什么'），"
            "拆分为独立的子问题\n"
            "3. 每个子问题应是一个完整的、可独立检索的疑问句\n"
            "4. 只返回 JSON 数组，不要额外解释\n\n"
            "示例：\n"
            "问题: 'CSDC 登记和结算分别怎么操作'\n"
            '输出: ["CSDC 登记操作流程", "CSDC 结算操作流程"]\n\n'
            "问题: '中登公司的作用是什么'\n"
            '输出: ["中登公司的作用是什么"]\n\n'
            "问题: '清算和交收系统的运行规则'\n"
            '输出: ["清算和交收系统的运行规则"]\n\n'
            "问题: '上交所和深交所的交易规则分别是什么'\n"
            '输出: ["上交所交易规则", "深交所交易规则"]'
        )

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"问题: {text}"},
                ],
                temperature=0.1,
                max_tokens=256,
            )

            content = resp.choices[0].message.content or ""
            sub_questions = self._parse_llm_response(content)

            if sub_questions:
                logger.info(f"LLM 多意图分解: {len(sub_questions)} 子问题")
                return sub_questions

            return []
        except Exception as e:
            logger.warning(f"LLM 分解调用失败: {e}")
            return []

    def _parse_llm_response(self, content: str) -> list[str]:
        """解析 LLM 返回的子问题列表

        支持 JSON 数组和换行分隔两种格式。
        """
        # 尝试 JSON 解析
        # 提取 JSON 数组部分
        json_match = re.search(r"\[.*?\]", content, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
                    return [q.strip() for q in parsed if q.strip()]
            except (json.JSONDecodeError, ValueError):
                pass

        # 尝试换行分隔（每行一个子问题）
        lines = [line.strip().strip('"').strip("'") for line in content.split("\n") if line.strip()]
        lines = [
            re.sub(r"^\d+[.、)]\s*", "", line)  # 去掉序号
            for line in lines
        ]
        if len(lines) >= 1:
            return lines

        return []

    def get_term_map(self) -> dict[str, str]:
        """获取当前加载的术语映射表"""
        return dict(self._term_map)


def create_query_rewriter(
    term_map_path: str = "./data/term_map.json",
    model: str = "",
    timeout_seconds: float = 3.0,
    enabled: bool = True,
) -> QueryRewriter:
    """创建 QueryRewriter 实例"""
    return QueryRewriter(
        term_map_path=term_map_path,
        model=model,
        timeout_seconds=timeout_seconds,
        enabled=enabled,
    )
