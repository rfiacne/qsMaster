"""
Early Exit 模块 — 标准答案库精确/模糊匹配

在问答 Pipeline 前端设置匹配路由器，对标准答案库命中的问题
直接返回标准答案，跳过 LLM 生成和 Faithfulness 校验。

功能:
  - StandardAnswer 数据模型
  - StandardAnswerStore: JSON 文件持久化存储
  - EarlyExitMatcher: 精确匹配 + 语义模糊匹配（可配置阈值）

用法:
    matcher = EarlyExitMatcher()
    result = matcher.match("沪深交易所 A 股清算周期是多少？")
    if result:
        print(f"标准答案命中: {result.answer}")
    else:
        print("未命中，走正常流程")
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── 数据模型 ───────────────────────────────────────────────


@dataclass
class AliasQuestion:
    """标准答案的别名问题

    Attributes:
        question: 别名问题文本
        similarity_score: 与原始问题的语义相似度 (0~1)
        created_at: 创建时间
    """

    question: str = ""
    similarity_score: float = 0.0
    created_at: str = ""


@dataclass
class AuditEntry:
    """操作审计记录

    Attributes:
        action: 操作类型 (create/update/disable/enable/delete/add_alias/remove_alias)
        field: 变更字段名
        old_value: 变更前值 (JSON)
        new_value: 变更后值 (JSON)
        operator: 操作人
        timestamp: 操作时间
    """

    action: str = ""
    field: str = ""
    old_value: str = ""
    new_value: str = ""
    operator: str = "system"
    timestamp: str = ""


@dataclass
class StandardAnswer:
    """标准答案库中的问答对

    Attributes:
        id: 唯一标识（由问题文本哈希生成）
        question: 标准问题
        answer: 标准答案
        category: 类别标签
        tags: 标签列表
        effective_date: 生效日期 (YYYY-MM-DD)
        source: 录入来源（如 "expert", "seed", "import"）
        match_strategy: 匹配策略 ("exact"=仅精确, "fuzzy"=仅模糊, "both"=两者)
        status: 状态 ("enabled"=启用, "disabled"=禁用, "candidate"=候选)
        aliases: 别名问题列表
        audit_log: 操作审计记录列表
        created_at: 录入时间 (ISO datetime)
        updated_at: 更新时间 (ISO datetime)
    """

    id: str = ""
    question: str = ""
    answer: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    effective_date: str = ""
    source: str = ""
    match_strategy: str = "both"
    status: str = "enabled"
    aliases: list[AliasQuestion] = field(default_factory=list)
    audit_log: list[AuditEntry] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StandardAnswer:
        """从字典创建"""
        aliases_raw = data.get("aliases", [])
        aliases = []
        if isinstance(aliases_raw, list):
            aliases = [AliasQuestion(**a) if isinstance(a, dict) else a for a in aliases_raw]
        audit_raw = data.get("audit_log", [])
        audit_log = []
        if isinstance(audit_raw, list):
            audit_log = [AuditEntry(**a) if isinstance(a, dict) else a for a in audit_raw]
        return cls(
            id=data.get("id", ""),
            question=data.get("question", ""),
            answer=data.get("answer", ""),
            category=data.get("category", ""),
            tags=data.get("tags", []),
            effective_date=data.get("effective_date", ""),
            source=data.get("source", ""),
            match_strategy=data.get("match_strategy", "both"),
            status=data.get("status", "enabled"),
            aliases=aliases,
            audit_log=audit_log,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """转字典（序列化用）"""
        d = asdict(self)
        # 确保 audit_log 中的复杂类型被正确序列化
        return d

    @staticmethod
    def generate_id(question: str) -> str:
        """从问题文本生成唯一 ID"""
        return hashlib.sha256(question.strip().encode("utf-8")).hexdigest()[:16]


# ─── 持久化存储 ───────────────────────────────────────────────


class StandardAnswerStore:
    """标准答案库持久化存储

    使用 JSON 文件存储，包含内存索引和线程安全锁。
    支持按 ID/问题/类别查询和 CRUD 操作。

    存储结构:
        data/standard_answers/
            answers.json       # 主存储文件
            embeddings_cache/  # 嵌入缓存（可选）
    """

    def __init__(self, store_path: str = "./data/standard_answers"):
        self.store_path = Path(store_path)
        self.answers_file = self.store_path / "answers.json"
        self._answers: dict[str, StandardAnswer] = {}  # id → answer
        self._lock = threading.Lock()
        self._loaded = False

    def load(self) -> None:
        """从磁盘加载标准答案库"""
        if self._loaded:
            return

        with self._lock:
            if self._loaded:  # double-check
                return

            if self.answers_file.exists():
                try:
                    with open(self.answers_file, encoding="utf-8") as f:
                        raw = json.load(f)
                    for item in raw:
                        answer = StandardAnswer.from_dict(item)
                        self._answers[answer.id] = answer
                    logger.info(f"标准答案库加载完成: {len(self._answers)} 条")
                except (OSError, json.JSONDecodeError) as e:
                    logger.error(f"标准答案库加载失败: {e}")
            else:
                logger.info("标准答案库为空（文件不存在），将自动创建")

            self._loaded = True

    def save(self) -> None:
        """持久化到磁盘"""
        self.store_path.mkdir(parents=True, exist_ok=True)

        with self._lock:
            data = [a.to_dict() for a in self._answers.values()]
            tmp_file = self.answers_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_file.replace(self.answers_file)

        logger.debug(f"标准答案库已保存: {len(data)} 条 → {self.answers_file}")

    # ─── CRUD ───────────────────────────────────────────────

    def add(self, answer: StandardAnswer) -> bool:
        """添加一条标准答案

        Returns:
            True 新增, False 覆盖已有
        """
        if not answer.id:
            answer.id = StandardAnswer.generate_id(answer.question)

        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        answer.created_at = answer.created_at or now
        answer.updated_at = now

        with self._lock:
            exists = answer.id in self._answers
            self._answers[answer.id] = answer

        self.save()
        logger.info(
            f"{'覆盖' if exists else '新增'}标准答案: {answer.id[:8]}... {answer.question[:40]}"
        )
        return not exists

    def remove(self, answer_id: str) -> bool:
        """删除一条标准答案"""
        with self._lock:
            if answer_id not in self._answers:
                return False
            del self._answers[answer_id]

        self.save()
        logger.info(f"删除标准答案: {answer_id[:8]}...")
        return True

    def get(self, answer_id: str) -> StandardAnswer | None:
        """按 ID 获取"""
        with self._lock:
            return self._answers.get(answer_id)

    def get_by_question(self, question: str) -> StandardAnswer | None:
        """按精确问题文本查找（标准化的精确匹配）"""
        normalized = self._normalize(question)
        with self._lock:
            for a in self._answers.values():
                if self._normalize(a.question) == normalized:
                    return a
        return None

    def list_all(self) -> list[StandardAnswer]:
        """列出所有标准答案"""
        with self._lock:
            return list(self._answers.values())

    def list_by_category(self, category: str) -> list[StandardAnswer]:
        """按类别列出"""
        with self._lock:
            return [a for a in self._answers.values() if a.category == category]

    def list_enabled(self) -> list[StandardAnswer]:
        """列出所有启用状态的标准答案"""
        with self._lock:
            return [a for a in self._answers.values() if a.status == "enabled"]

    def list_by_status(self, status: str) -> list[StandardAnswer]:
        """按状态列出"""
        with self._lock:
            return [a for a in self._answers.values() if a.status == status]

    def get_by_alias(self, question: str) -> StandardAnswer | None:
        """按别名问题查找所属的标准答案"""
        normalized = StandardAnswerStore._normalize(question)
        with self._lock:
            for a in self._answers.values():
                if a.status != "enabled":
                    continue
                for alias in a.aliases:
                    if StandardAnswerStore._normalize(alias.question) == normalized:
                        return a
        return None

    def set_status(self, answer_id: str, status: str, operator: str = "system") -> bool:
        """设置标准答案状态（enabled/disabled/candidate）

        Returns:
            True 成功, False 未找到
        """
        if status not in ("enabled", "disabled", "candidate"):
            return False
        with self._lock:
            a = self._answers.get(answer_id)
            if not a:
                return False
            old_status = a.status
            a.status = status
            a.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            a.audit_log.append(
                AuditEntry(
                    action=(
                        "disable"
                        if status == "disabled"
                        else "enable"
                        if status == "enabled"
                        else "candidate"
                    ),
                    field="status",
                    old_value=old_status,
                    new_value=status,
                    operator=operator,
                    timestamp=a.updated_at,
                )
            )
        self.save()
        logger.info(f"标准答案 {answer_id[:8]}... 状态: {old_status} → {status}")
        return True

    def add_alias(
        self,
        answer_id: str,
        alias_question: str,
        similarity_score: float = 1.0,
        operator: str = "system",
    ) -> bool:
        """为标准答案添加别名问题"""
        with self._lock:
            a = self._answers.get(answer_id)
            if not a:
                return False
            # 检查是否已存在
            normalized_new = StandardAnswerStore._normalize(alias_question)
            for existing in a.aliases:
                if StandardAnswerStore._normalize(existing.question) == normalized_new:
                    return True  # 已存在，不重复添加
            alias = AliasQuestion(
                question=alias_question.strip(),
                similarity_score=similarity_score,
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            a.aliases.append(alias)
            a.updated_at = alias.created_at
            a.audit_log.append(
                AuditEntry(
                    action="add_alias",
                    field="aliases",
                    old_value="",
                    new_value=alias_question,
                    operator=operator,
                    timestamp=alias.created_at,
                )
            )
        self.save()
        logger.info(f"标准答案 {answer_id[:8]}... 添加别名: {alias_question[:40]}")
        return True

    def remove_alias(self, answer_id: str, alias_question: str) -> bool:
        """移除别名问题"""
        with self._lock:
            a = self._answers.get(answer_id)
            if not a:
                return False
            normalized = StandardAnswerStore._normalize(alias_question)
            before = len(a.aliases)
            a.aliases = [
                al for al in a.aliases if StandardAnswerStore._normalize(al.question) != normalized
            ]
            if len(a.aliases) == before:
                return False
            a.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            a.audit_log.append(
                AuditEntry(
                    action="remove_alias",
                    field="aliases",
                    old_value=alias_question,
                    new_value="",
                    operator="system",
                    timestamp=a.updated_at,
                )
            )
        self.save()
        return True

    def count(self) -> int:
        """总数"""
        with self._lock:
            return len(self._answers)

    def search(self, keyword: str) -> list[StandardAnswer]:
        """关键词搜索（在问题和答案中匹配）"""
        kw = keyword.lower().strip()
        if not kw:
            return []

        with self._lock:
            results = []
            for a in self._answers.values():
                if kw in a.question.lower() or kw in a.answer.lower() or kw in a.category.lower():
                    results.append(a)
            return results

    # ─── 批量操作 ───────────────────────────────────────────────

    def import_batch(self, items: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
        """批量导入

        Returns:
            (新增数, 覆盖数, 错误列表)
        """
        added = 0
        overwritten = 0
        errors: list[str] = []

        for i, item in enumerate(items):
            try:
                if not item.get("question") or not item.get("answer"):
                    errors.append(f"第 {i + 1} 条缺少 question 或 answer 字段")
                    continue

                answer = StandardAnswer.from_dict(item)
                if not answer.id:
                    answer.id = StandardAnswer.generate_id(answer.question)

                now = time.strftime("%Y-%m-%dT%H:%M:%S")
                answer.created_at = answer.created_at or now
                answer.updated_at = now

                with self._lock:
                    was_new = answer.id not in self._answers
                    self._answers[answer.id] = answer

                if was_new:
                    added += 1
                else:
                    overwritten += 1

            except Exception as e:
                errors.append(f"第 {i + 1} 条导入失败: {e}")

        if added + overwritten > 0:
            self.save()

        return added, overwritten, errors

    def export_all(self) -> list[dict[str, Any]]:
        """导出所有标准答案（字典列表）"""
        return [a.to_dict() for a in self.list_all()]

    def clear(self) -> int:
        """清空所有标准答案"""
        with self._lock:
            count = len(self._answers)
            self._answers.clear()
        if count > 0:
            self.save()
        return count

    # ─── 工具 ───────────────────────────────────────────────

    @staticmethod
    def _normalize(text: str) -> str:
        """标准化文本用于匹配

        去除首尾空格、统一标点符号、全角转半角、小写化。
        """
        import re

        t = text.strip()
        # 全角转半角
        result = []
        for ch in t:
            code = ord(ch)
            if 0xFF01 <= code <= 0xFF5E:
                result.append(chr(code - 0xFEE0))
            elif code == 0x3000:
                result.append(" ")
            else:
                result.append(ch)
        t = "".join(result)
        # 统一中文/英文标点（保留文字）
        t = t.replace("？", "?").replace("！", "!").replace("，", ",").replace("：", ":")
        t = t.replace("；", ";").replace("（", "(").replace("）", ")").replace("、", ",")
        # 压缩空白
        t = re.sub(r"\s+", " ", t)
        # 小写
        t = t.lower()
        return t

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# ─── 匹配器 ───────────────────────────────────────────────


@dataclass
class MatchResult:
    """匹配结果"""

    matched: bool = False
    answer: StandardAnswer = field(default_factory=StandardAnswer)
    match_type: str = ""  # "exact" | "fuzzy"
    score: float = 0.0


class EarlyExitMatcher:
    """Early Exit 匹配器

    在问答 Pipeline 前端使用，检查用户问题是否匹配标准答案库。
    支持两层匹配策略:
    1. 精确匹配（标准化后字符串完全一致）
    2. 模糊匹配（嵌入向量余弦相似度 ≥ 阈值）

    用法:
        matcher = EarlyExitMatcher(
            store_path="./data/standard_answers",
            fuzzy_threshold=0.85,
        )
        result = matcher.match("沪深交易所 A 股清算周期？")
        if result.matched:
            return result.answer
    """

    def __init__(
        self,
        store_path: str = "./data/standard_answers",
        fuzzy_threshold: float = 0.82,
        enabled: bool = True,
    ):
        """
        Args:
            store_path: 标准答案库存储路径
            fuzzy_threshold: 模糊匹配相似度阈值 (0.0~1.0)，低于此值不命中
            enabled: 是否启用 Early Exit
        """
        self.store = StandardAnswerStore(store_path)
        self.fuzzy_threshold = fuzzy_threshold
        self.enabled = enabled
        self.store.load()

        # 嵌入缓存：question_hash → embedding
        self._embedding_cache: dict[str, list[float]] = {}
        # 问题列表：id → (normalized_question, embedding_hash)
        self._question_index: dict[str, tuple[str, str]] = {}
        self._cache_lock = threading.Lock()
        self._index_built = False

    def match(self, question: str) -> MatchResult:
        """匹配用户问题到标准答案库

        1. 先尝试精确匹配（标准化后字符串相等）
        2. 再尝试模糊匹配（嵌入相似度 ≥ fuzzy_threshold）

        Returns:
            MatchResult: matched=True + answer 当匹配命中
                          matched=False 当未命中或 disabled
        """
        if not self.enabled or not question.strip():
            return MatchResult()

        self._ensure_loaded()

        normalized = StandardAnswerStore._normalize(question)

        # 1) 精确匹配
        exact_hit = self._match_exact(normalized)
        if exact_hit is not None:
            logger.info(f"Early Exit 精确匹配命中: {exact_hit.id[:8]}...")
            return MatchResult(
                matched=True,
                answer=exact_hit,
                match_type="exact",
                score=1.0,
            )

        # 2) 模糊匹配
        fuzzy_hit = self._match_fuzzy(question)
        if fuzzy_hit is not None:
            answer, score = fuzzy_hit
            logger.info(
                f"Early Exit 模糊匹配命中: {answer.id[:8]}... "
                f"score={score:.4f} (threshold={self.fuzzy_threshold})"
            )
            return MatchResult(
                matched=True,
                answer=answer,
                match_type="fuzzy",
                score=score,
            )

        return MatchResult()

    def match_batch(self, questions: list[str]) -> list[MatchResult]:
        """批量匹配——单次批量嵌入所有问题，减少 embedding API 往返

        对模糊匹配的问题一次性调用 embed_texts 进行批量嵌入，
        然后逐个匹配，复用计算结果。
        """
        if not questions:
            return []

        # 分离精确匹配和模糊匹配的问题
        results: list[MatchResult] = []
        fuzzy_questions: list[tuple[int, str]] = []  # (index, question)

        for i, q in enumerate(questions):
            if not self.enabled or not q.strip():
                results.append(MatchResult())
                continue

            self._ensure_loaded()
            normalized = StandardAnswerStore._normalize(q)

            # 先尝试精确匹配
            exact_hit = self._match_exact(normalized)
            if exact_hit is not None:
                results.append(
                    MatchResult(
                        matched=True,
                        answer=exact_hit,
                        match_type="exact",
                        score=1.0,
                    )
                )
            else:
                fuzzy_questions.append((i, q))
                # 占位，稍后填充
                results.append(MatchResult())

        if not fuzzy_questions:
            return results

        # 批量嵌入所有需要模糊匹配的问题
        self._build_index()
        self._ensure_loaded()

        try:
            from qa.pipelines.components.embedder import embed_texts

            fuzzy_texts = [q for _, q in fuzzy_questions]
            batch_embeddings = embed_texts(fuzzy_texts)

            # 逐个模糊匹配
            for (orig_idx, question), query_embedding in zip(fuzzy_questions, batch_embeddings):
                if not query_embedding:
                    continue
                best_answer: StandardAnswer | None = None
                best_score = 0.0

                for answer in self.store.list_enabled():
                    if answer.match_strategy not in ("fuzzy", "both"):
                        continue
                    cached = self._embedding_cache.get(answer.id)
                    if not cached:
                        continue
                    score = self._cosine_similarity(query_embedding, cached)
                    if score > best_score:
                        best_score = score
                        best_answer = answer

                if best_answer and best_score >= self.fuzzy_threshold:
                    results[orig_idx] = MatchResult(
                        matched=True,
                        answer=best_answer,
                        match_type="fuzzy",
                        score=best_score,
                    )
        except Exception as e:
            logger.error(f"批量嵌入匹配失败: {e}")
            # 不重试单次匹配，直接返回已有结果

        return results

    # ─── 精确匹配 ───────────────────────────────────────────────

    def _match_exact(self, normalized_question: str) -> StandardAnswer | None:
        """精确匹配：标准化问题文本完全一致

        1. 匹配主问题（只匹配 enabled 状态）
        2. 匹配别名问题
        """
        for answer in self.store.list_enabled():
            if answer.match_strategy in ("exact", "both"):
                stored_normalized = StandardAnswerStore._normalize(answer.question)
                if stored_normalized == normalized_question:
                    return answer
                # 搜索别名
                for alias in answer.aliases:
                    alias_normalized = StandardAnswerStore._normalize(alias.question)
                    if alias_normalized == normalized_question:
                        return answer
        return None

    # ─── 模糊匹配 ───────────────────────────────────────────────

    def _match_fuzzy(self, question: str) -> tuple[StandardAnswer, float] | None:
        """模糊匹配：嵌入向量余弦相似度 ≥ 阈值

        只对 match_strategy 为 "fuzzy" 或 "both" 的条目进行匹配。
        """
        self._build_index()

        if not self._index_built:
            return None

        # 嵌入用户问题
        query_embedding = self._embed_question(question)
        if not query_embedding:
            return None

        best_answer: StandardAnswer | None = None
        best_score = 0.0

        for answer in self.store.list_enabled():
            if answer.match_strategy not in ("fuzzy", "both"):
                continue

            # 获取缓存嵌入
            cached = self._embedding_cache.get(answer.id)
            if not cached:
                continue

            score = self._cosine_similarity(query_embedding, cached)
            if score > best_score:
                best_score = score
                best_answer = answer

        if best_answer and best_score >= self.fuzzy_threshold:
            return (best_answer, best_score)

        return None

    def _build_index(self) -> None:
        """构建嵌入缓存索引（为需要模糊匹配的条目计算嵌入）"""
        if self._index_built:
            return

        with self._cache_lock:
            if self._index_built:
                return

            fuzzy_answers = [
                a for a in self.store.list_enabled() if a.match_strategy in ("fuzzy", "both")
            ]

            if not fuzzy_answers:
                logger.debug("没有需要模糊匹配的标准答案条目")
                self._index_built = True
                return

            # 收集主问题 + 别名问题一起嵌入
            questions = [a.question for a in fuzzy_answers]
            # 构建别名索引：alias_text → answer_id（避免 O(n²) 的 .index() 查找）
            alias_index: dict[str, tuple[str, int]] = {}
            alias_offset = len(fuzzy_answers)
            for a in fuzzy_answers:
                for alias in a.aliases:
                    alias_index[alias.question] = (a.id, alias_offset)
                    alias_offset += 1
                    questions.append(alias.question)
            try:
                from qa.pipelines.components.embedder import embed_texts

                embeddings = embed_texts(questions)
                # 主问题嵌入
                for answer, emb in zip(fuzzy_answers, embeddings[: len(fuzzy_answers)]):
                    self._embedding_cache[answer.id] = emb
                # 别名问题嵌入（通过预构建的 alias_index O(1) 查找）
                for alias_text, (aid, idx) in alias_index.items():
                    if idx < len(embeddings):
                        cache_key = f"{aid}_alias_{hash(alias_text) % 100000}"
                        self._embedding_cache[cache_key] = embeddings[idx]
                logger.info(f"模糊匹配索引构建完成: {len(fuzzy_answers)} 条标准答案")
            except Exception as e:
                logger.error(f"模糊匹配索引构建失败（将跳过模糊匹配）: {e}")

            self._index_built = True

    def _embed_question(self, question: str) -> list[float] | None:
        """嵌入单个问题"""
        try:
            from qa.pipelines.components.embedder import embed_query

            return embed_query(question)
        except Exception as e:
            logger.error(f"嵌入查询失败（模糊匹配跳过）: {e}")
            return None

    def rebuild_index(self) -> None:
        """重建嵌入缓存索引（添加/更新标准答案后调用）"""
        with self._cache_lock:
            self._embedding_cache.clear()
            self._question_index.clear()
            self._index_built = False
        self._build_index()

    # ─── 工具 ───────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """确保标准答案库已加载"""
        if not self.store.is_loaded:
            self.store.load()

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        from qa.utils import cosine_similarity

        return cosine_similarity(a, b)

    @property
    def is_enabled(self) -> bool:
        return self.enabled and self.store.count() > 0
