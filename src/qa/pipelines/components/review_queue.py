"""
审核队列组件 — ReviewItem 数据模型 + ReviewStore + ReviewWorkflow

在 Faithfulness Evaluator 判定 FAIL 时，问答自动进入审核队列。
业务专家通过 CLI 标注"正确/部分正确/错误"，标注结果反馈到标准答案库。

功能:
  - ReviewItem 数据模型（含问题/回答/引用/Eval 分数/审核状态）
  - ReviewStore: JSON 文件持久化存储
  - ReviewWorkflow: 自动入队 + 三级标注 + 归档 + 统计

用法:
    workflow = ReviewWorkflow()
    workflow.add_item(question, answer, sources, faithfulness_report)
    workflow.label(item_id, "correct", reviewer="张三")
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── 枚举 ───────────────────────────────────────────────




class ReviewStatus(StrEnum):
    """审核状态"""
    PENDING = "pending"
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    ARCHIVED = "archived"


class Priority(StrEnum):
    """审核优先级"""
    HIGH = "high"      # Faithfulness FAIL
    NORMAL = "normal"  # 常规问答


# ─── 数据模型 ───────────────────────────────────────────────


@dataclass
class ReviewItem:
    """审核队列项

    Attributes:
        id: 唯一标识
        question: 用户原始问题
        answer: 系统生成的回答
        sources: 引用来源列表（字典格式）
        faithfulness_score: Faithfulness Evaluator 分数 (0~1)
        faithfulness_result: Evaluator 结果 (pass/partial/fail)
        priority: 优先级
        status: 审核状态
        label: 人工标注 (correct/partial/incorrect)
        reviewer: 审核人
        review_comment: 修正意见
        reviewed_at: 审核时间
        created_at: 入队时间
        archived_at: 归档时间
    """
    id: str = ""
    question: str = ""
    answer: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    faithfulness_score: float = 0.0
    faithfulness_result: str = ""
    priority: str = "normal"
    status: str = "pending"
    label: str = ""
    reviewer: str = ""
    review_comment: str = ""
    reviewed_at: str = ""
    created_at: str = ""
    archived_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewItem:
        return cls(
            id=data.get("id", ""),
            question=data.get("question", ""),
            answer=data.get("answer", ""),
            sources=data.get("sources", []),
            faithfulness_score=data.get("faithfulness_score", 0.0),
            faithfulness_result=data.get("faithfulness_result", ""),
            priority=data.get("priority", "normal"),
            status=data.get("status", "pending"),
            label=data.get("label", ""),
            reviewer=data.get("reviewer", ""),
            review_comment=data.get("review_comment", ""),
            reviewed_at=data.get("reviewed_at", ""),
            created_at=data.get("created_at", ""),
            archived_at=data.get("archived_at", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewStats:
    """审核统计快照"""
    total: int = 0
    pending: int = 0
    correct: int = 0
    partial: int = 0
    incorrect: int = 0
    archived: int = 0
    completion_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── 持久化存储 ───────────────────────────────────────────────


class ReviewStore:
    """审核队列持久化存储

    使用 JSON 文件存储，线程安全。
    """

    def __init__(self, store_path: str = "./data/review_queue"):
        self.store_path = Path(store_path)
        self.items_file = self.store_path / "items.json"
        self._items: dict[str, ReviewItem] = {}
        self._lock = threading.Lock()
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if self.items_file.exists():
                try:
                    with open(self.items_file, encoding="utf-8") as f:
                        raw = json.load(f)
                    self._items = {item["id"]: ReviewItem.from_dict(item) for item in raw}
                    logger.info(f"审核队列加载完成: {len(self._items)} 项")
                except (OSError, json.JSONDecodeError) as e:
                    logger.error(f"审核队列加载失败: {e}")
            else:
                logger.info("审核队列为空，将自动创建")
            self._loaded = True

    def save(self) -> None:
        self.store_path.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = [item.to_dict() for item in self._items.values()]
            tmp_file = self.items_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_file.replace(self.items_file)

    # ─── CRUD ───────────────────────────────────────────────

    def add(self, item: ReviewItem) -> str:
        """添加审核项"""
        if not item.id:
            import hashlib
            item.id = hashlib.sha256(
                f"{item.question}{time.time()}".encode()
            ).hexdigest()[:16]
        item.created_at = item.created_at or time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self._items[item.id] = item
        self.save()
        return item.id

    def get(self, item_id: str) -> ReviewItem | None:
        with self._lock:
            return self._items.get(item_id)

    def list_all(self, status: str | None = None) -> list[ReviewItem]:
        with self._lock:
            items = list(self._items.values())
        if status:
            if status in ("correct", "partial", "incorrect"):
                # 按 label 字段过滤（用于审核标注查询）
                items = [i for i in items if i.label == status]
            elif status == "archived":
                items = [i for i in items if i.status == "archived"]
            else:
                # pending / reviewed → 按 status 字段过滤
                items = [i for i in items if i.status == status]
        # 按优先级排序（HIGH 优先），再按时间倒序
        priority_order = {"high": 0, "normal": 1}
        items.sort(key=lambda i: (priority_order.get(i.priority, 9), i.created_at or ""))
        return items

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def remove(self, item_id: str) -> bool:
        with self._lock:
            if item_id not in self._items:
                return False
            del self._items[item_id]
        self.save()
        return True

    def get_stats(self) -> ReviewStats:
        """获取审核统计"""
        with self._lock:
            items = list(self._items.values())
        total = len(items)
        pending = sum(1 for i in items if i.status == "pending")
        correct = sum(1 for i in items if i.label == "correct")
        partial = sum(1 for i in items if i.label == "partial")
        incorrect = sum(1 for i in items if i.label == "incorrect")
        archived = sum(1 for i in items if i.status == "archived")
        # 完成率 = 已审核（correct + partial + incorrect）/ 总数，归档不计入完成
        reviewed_count = correct + partial + incorrect
        completion_rate = reviewed_count / total if total > 0 else 0.0
        return ReviewStats(
            total=total, pending=pending,
            correct=correct, partial=partial,
            incorrect=incorrect, archived=archived,
            completion_rate=round(completion_rate, 4),
        )

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# ─── 工作流 ───────────────────────────────────────────────


class ReviewWorkflow:
    """审核工作流

    自动入队 → 人工标注 → 语义匹配 → 归档/入库

    当标注为"正确"时，自动进行语义匹配：
    - 相似度 ≥ threshold → 作为别名添加到已有标准答案
    - 相似度 < threshold → 创建 status="candidate" 的新标准答案
    """

    def __init__(
        self,
        store_path: str = "./data/review_queue",
        semantic_threshold: float = 0.85,
        candidate_confirm_count: int = 1,
    ):
        self.store = ReviewStore(store_path)
        self.semantic_threshold = semantic_threshold
        self.candidate_confirm_count = candidate_confirm_count
        self._embedding_cache: dict[str, list[float]] = {}

    def ensure_loaded(self) -> None:
        if not self.store.is_loaded:
            self.store.load()

    def add_item(
        self,
        question: str,
        answer: str,
        sources: list[dict[str, Any]] | None = None,
        faithfulness_score: float = 0.0,
        faithfulness_result: str = "",
        priority: str = "normal",
    ) -> str:
        """添加审核项到队列

        通常由 QueryPipeline 在 Faithfulness FAIL 后自动调用。
        """
        self.ensure_loaded()
        item = ReviewItem(
            question=question,
            answer=answer,
            sources=sources or [],
            faithfulness_score=faithfulness_score,
            faithfulness_result=faithfulness_result,
            priority=priority,
            status="pending",
        )
        item_id = self.store.add(item)
        logger.info(
            f"审核队列新增: {item_id[:8]}... "
            f"priority={priority}, faithfulness={faithfulness_score:.2f}"
        )
        return item_id

    def label(
        self,
        item_id: str,
        label: str,
        reviewer: str = "",
        comment: str = "",
        auto_convert: bool = True,
        standard_answer_store: Any | None = None,
    ) -> bool:
        """标注审核项

        Args:
            item_id: 审核项 ID
            label: 标注结果 (correct/partial/incorrect)
            reviewer: 审核人
            comment: 修正意见
            auto_convert: 标注为 correct 时是否自动进行语义匹配入库
            standard_answer_store: StandardAnswerStore 实例（auto_convert 时需要）

        Returns:
            bool: 是否成功
        """
        if label not in ("correct", "partial", "incorrect"):
            logger.warning(f"无效标注: {label}")
            return False

        self.ensure_loaded()
        item = self.store.get(item_id)
        if not item:
            logger.warning(f"审核项不存在: {item_id[:8]}...")
            return False

        if item.status != "pending":
            logger.warning(f"审核项 {item_id[:8]}... 状态为 {item.status}，不可重复标注")
            return False

        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        item.status = "reviewed"
        item.label = label
        item.reviewer = reviewer
        item.review_comment = comment
        item.reviewed_at = now

        # 语义匹配自动入库（FR-007~FR-010）
        if label == "correct" and auto_convert and standard_answer_store is not None:
            try:
                self._auto_convert(item.question, standard_answer_store)
            except Exception as e:
                logger.error(f"语义匹配自动入库失败: {e}")

        self.store.save()
        logger.info(
            f"审核标注完成: {item_id[:8]}... "
            f"label={label}, reviewer={reviewer}"
        )
        return True

    def _auto_convert(
        self,
        question: str,
        std_store: Any,
    ) -> None:
        """自动语义匹配入库

        1. 嵌入问题文本
        2. 与所有 enabled 标准答案计算余弦相似度
        3. 最高分 ≥ threshold → 添加为别名
        4. 最高分 < threshold → 创建 candidate
        """
        if not std_store.is_loaded:
            std_store.load()

        enabled = std_store.list_enabled()
        if not enabled:
            # 没有现有标准答案，直接创建
            from qa.pipelines.components.early_exit import StandardAnswer
            new_a = StandardAnswer(
                question=question,
                answer="",
                status="candidate",
                source="review_auto",
            )
            std_store.add(new_a)
            logger.info(f"语义匹配：无现有标准答案，创建候选: {question[:40]}")
            return

        # 嵌入问题
        query_emb = self._embed_question(question)
        if query_emb is None:
            logger.warning("语义匹配：嵌入失败，跳过自动入库")
            return

        # 计算与所有 enabled 标准答案的相似度
        best_score = 0.0
        best_answer = enabled[0]
        for answer in enabled:
            stored_emb = self._get_answer_embedding(answer, std_store)
            if stored_emb is None:
                continue
            score = self._cosine_similarity(query_emb, stored_emb)
            if score > best_score:
                best_score = score
                best_answer = answer

        from qa.pipelines.components.early_exit import StandardAnswer

        if best_score >= self.semantic_threshold:
            # 添加为别名
            std_store.add_alias(
                best_answer.id, question,
                similarity_score=best_score,
                operator="review_auto",
            )
            logger.info(
                f"语义匹配：添加别名 (score={best_score:.3f}) "
                f"→ {best_answer.question[:30]}"
            )
        else:
            # 创建候选
            new_a = StandardAnswer(
                question=question,
                answer="",
                status="candidate",
                source="review_auto",
            )
            std_store.add(new_a)
            logger.info(
                f"语义匹配：创建候选 (best_score={best_score:.3f}, "
                f"threshold={self.semantic_threshold}) → {question[:40]}"
            )

    def _embed_question(self, question: str) -> list[float] | None:
        """嵌入问题文本"""
        try:
            from qa.pipelines.components.embedder import embed_query
            return embed_query(question)
        except Exception as e:
            logger.error(f"语义匹配嵌入失败: {e}")
            return None

    def _get_answer_embedding(
        self, answer: Any, std_store: Any,
    ) -> list[float] | None:
        """获取标准答案的问题嵌入（缓存）"""
        if answer.id in self._embedding_cache:
            return self._embedding_cache[answer.id]
        try:
            from qa.pipelines.components.embedder import embed_query
            emb = embed_query(answer.question)
            self._embedding_cache[answer.id] = emb
            return emb
        except Exception as e:
            logger.error(f"标准答案嵌入失败: {e}")
            return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        from qa.utils import cosine_similarity

        return cosine_similarity(a, b)

    def archive_old(self, max_days: int = 90) -> int:
        """归档超过保留期限的审核项"""
        self.ensure_loaded()
        items = self.store.list_all()
        now = time.time()
        archived = 0
        for item in items:
            if item.status != "pending":
                continue
            if not item.created_at:
                continue
            try:
                created = time.strptime(item.created_at, "%Y-%m-%dT%H:%M:%S")
                age_days = (now - time.mktime(created)) / 86400
                if age_days > max_days:
                    item.status = "archived"
                    item.archived_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                    archived += 1
            except (ValueError, OSError):
                continue
        if archived > 0:
            self.store.save()
            logger.info(f"审核队列归档: {archived} 项超过 {max_days} 天")
        return archived

    def get_stats(self) -> ReviewStats:
        self.ensure_loaded()
        return self.store.get_stats()
