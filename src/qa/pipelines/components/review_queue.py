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
import sqlite3
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

    HIGH = "high"  # Faithfulness FAIL
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

    使用 SQLite 存储，行级写入，避免 JSON 全量写放大。
    旧 JSON 文件 data/review_queue/items.json 自动迁移到 db_path。
    """

    def __init__(self, store_path: str = "./data/review_queue"):
        self._db_path = Path(store_path) / "review_queue.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

        # 自动迁移旧 JSON 数据
        json_path = Path(store_path) / "items.json"
        if json_path.exists() and not self._db_path.exists():
            try:
                with open(json_path, encoding="utf-8") as f:
                    raw = json.load(f)
                if raw:
                    self._migrate_from_json(raw)
                    backup = json_path.with_suffix(".json.bak")
                    json_path.rename(backup)
                    logger.info(f"旧 JSON 数据已自动迁移到 SQLite: {json_path} → {self._db_path}")
            except Exception as e:
                logger.warning(f"JSON 自动迁移失败（跳过）: {e}")

    def _get_conn(self) -> sqlite3.Connection:
        """获取 SQLite 连接（惰性初始化）"""
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    self._conn = sqlite3.connect(
                        str(self._db_path), check_same_thread=False
                    )
                    self._conn.row_factory = sqlite3.Row
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA synchronous=NORMAL")
                    self._conn.execute("""
                        CREATE TABLE IF NOT EXISTS review_items (
                            id TEXT PRIMARY KEY,
                            question TEXT NOT NULL,
                            answer TEXT NOT NULL DEFAULT '',
                            sources TEXT NOT NULL DEFAULT '[]',
                            faithfulness_score REAL NOT NULL DEFAULT 0.0,
                            faithfulness_result TEXT NOT NULL DEFAULT '',
                            priority TEXT NOT NULL DEFAULT 'normal',
                            status TEXT NOT NULL DEFAULT 'pending',
                            label TEXT NOT NULL DEFAULT '',
                            reviewer TEXT NOT NULL DEFAULT '',
                            review_comment TEXT NOT NULL DEFAULT '',
                            reviewed_at TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL DEFAULT '',
                            archived_at TEXT NOT NULL DEFAULT ''
                        )
                    """)
                    self._conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_review_status
                        ON review_items(status)
                    """)
                    self._conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_review_label
                        ON review_items(label)
                    """)
                    self._conn.commit()
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> ReviewStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _migrate_from_json(self, raw: list[dict]) -> None:
        """从 JSON 列表批量导入到 SQLite"""
        conn = self._get_conn()
        for item in raw:
            conn.execute(
                """INSERT OR REPLACE INTO review_items
                   (id, question, answer, sources, faithfulness_score,
                    faithfulness_result, priority, status, label,
                    reviewer, review_comment, reviewed_at, created_at, archived_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.get("id", ""),
                    item.get("question", ""),
                    item.get("answer", ""),
                    json.dumps(item.get("sources", []), ensure_ascii=False),
                    item.get("faithfulness_score", 0.0),
                    item.get("faithfulness_result", ""),
                    item.get("priority", "normal"),
                    item.get("status", "pending"),
                    item.get("label", ""),
                    item.get("reviewer", ""),
                    item.get("review_comment", ""),
                    item.get("reviewed_at", ""),
                    item.get("created_at", ""),
                    item.get("archived_at", ""),
                ),
            )
        conn.commit()
        logger.info(f"SQLite 迁移完成: {len(raw)} 条记录")

    def load(self) -> None:
        """兼容原接口 — SQLite 无需加载到内存，空操作"""
        pass

    def save(self) -> None:
        """兼容原接口 — SQLite 行级写入即时持久化，空操作"""
        pass

    # ─── CRUD ───────────────────────────────────────────────

    def add(self, item: ReviewItem) -> str:
        """添加审核项（行级 SQLite 写入）"""
        if not item.id:
            import hashlib

            item.id = hashlib.sha256(f"{item.question}{time.time()}".encode()).hexdigest()[:16]
        item.created_at = item.created_at or time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT OR REPLACE INTO review_items
                   (id, question, answer, sources, faithfulness_score,
                    faithfulness_result, priority, status, label,
                    reviewer, review_comment, reviewed_at, created_at, archived_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.id,
                    item.question,
                    item.answer,
                    json.dumps(item.sources, ensure_ascii=False),
                    item.faithfulness_score,
                    item.faithfulness_result,
                    item.priority,
                    item.status,
                    item.label,
                    item.reviewer,
                    item.review_comment,
                    item.reviewed_at,
                    item.created_at,
                    item.archived_at,
                ),
            )
            conn.commit()
        return item.id

    def get(self, item_id: str) -> ReviewItem | None:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT * FROM review_items WHERE id = ?", (item_id,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_item(row)

    def list_all(self, status: str | None = None) -> list[ReviewItem]:
        with self._lock:
            conn = self._get_conn()
            if status:
                if status in ("correct", "partial", "incorrect"):
                    cur = conn.execute(
                        "SELECT * FROM review_items WHERE label = ? ORDER BY priority, created_at",
                        (status,),
                    )
                elif status == "archived":
                    cur = conn.execute(
                        "SELECT * FROM review_items WHERE status = 'archived' ORDER BY priority, created_at"
                    )
                else:
                    cur = conn.execute(
                        "SELECT * FROM review_items WHERE status = ? ORDER BY priority, created_at",
                        (status,),
                    )
            else:
                cur = conn.execute(
                    "SELECT * FROM review_items ORDER BY priority, created_at"
                )
            rows = cur.fetchall()
        return [self._row_to_item(r) for r in rows]

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> ReviewItem:
        return ReviewItem(
            id=row["id"],
            question=row["question"],
            answer=row["answer"],
            sources=json.loads(row["sources"] or "[]"),
            faithfulness_score=row["faithfulness_score"],
            faithfulness_result=row["faithfulness_result"],
            priority=row["priority"],
            status=row["status"],
            label=row["label"],
            reviewer=row["reviewer"],
            review_comment=row["review_comment"],
            reviewed_at=row["reviewed_at"],
            created_at=row["created_at"],
            archived_at=row["archived_at"],
        )

    def count(self) -> int:
        with self._lock:
            cur = self._get_conn().execute("SELECT COUNT(*) AS cnt FROM review_items")
            row = cur.fetchone()
        return row["cnt"] if row else 0

    def remove(self, item_id: str) -> bool:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("DELETE FROM review_items WHERE id = ?", (item_id,))
            conn.commit()
        return cur.rowcount > 0

    def update(self, item: ReviewItem) -> None:
        """更新审核项（行级 SQLite 写入）"""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """UPDATE review_items SET
                   answer=?, sources=?, faithfulness_score=?,
                   faithfulness_result=?, priority=?, status=?, label=?,
                   reviewer=?, review_comment=?, reviewed_at=?,
                   created_at=?, archived_at=?
                   WHERE id=?""",
                (
                    item.answer,
                    json.dumps(item.sources, ensure_ascii=False),
                    item.faithfulness_score,
                    item.faithfulness_result,
                    item.priority,
                    item.status,
                    item.label,
                    item.reviewer,
                    item.review_comment,
                    item.reviewed_at,
                    item.created_at,
                    item.archived_at,
                    item.id,
                ),
            )
            conn.commit()

    def get_stats(self) -> ReviewStats:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN label='correct' THEN 1 ELSE 0 END) AS correct,
                    SUM(CASE WHEN label='partial' THEN 1 ELSE 0 END) AS partial,
                    SUM(CASE WHEN label='incorrect' THEN 1 ELSE 0 END) AS incorrect,
                    SUM(CASE WHEN status='archived' THEN 1 ELSE 0 END) AS archived
                FROM review_items
            """)
            row = cur.fetchone()
        total = row["total"] or 0
        pending = row["pending"] or 0
        correct = row["correct"] or 0
        partial = row["partial"] or 0
        incorrect = row["incorrect"] or 0
        archived = row["archived"] or 0
        reviewed_count = correct + partial + incorrect
        completion_rate = reviewed_count / total if total > 0 else 0.0
        return ReviewStats(
            total=total,
            pending=pending,
            correct=correct,
            partial=partial,
            incorrect=incorrect,
            archived=archived,
            completion_rate=round(completion_rate, 4),
        )

    @property
    def is_loaded(self) -> bool:
        return True

    @property
    def _dirty(self) -> bool:
        """兼容旧 API — label() 和 archive_old() 仍设置此标志"""
        return False

    @_dirty.setter
    def _dirty(self, val: bool) -> None:
        pass  # SQLite 行级写入即时持久化，无需 dirty 标记


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

    def close(self) -> None:
        """关闭内部 ReviewStore 的 SQLite 连接"""
        self.store.close()

    def __enter__(self) -> ReviewWorkflow:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

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

        self.store.update(item)
        logger.info(f"审核标注完成: {item_id[:8]}... label={label}, reviewer={reviewer}")
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
                best_answer.id,
                question,
                similarity_score=best_score,
                operator="review_auto",
            )
            logger.info(
                f"语义匹配：添加别名 (score={best_score:.3f}) → {best_answer.question[:30]}"
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
        self,
        answer: Any,
        std_store: Any,
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
                    self.store.update(item)
                    archived += 1
            except (ValueError, OSError):
                continue
        if archived > 0:
            logger.info(f"审核队列归档: {archived} 项超过 {max_days} 天")
        return archived

    def get_stats(self) -> ReviewStats:
        self.ensure_loaded()
        return self.store.get_stats()
