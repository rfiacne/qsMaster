"""
审计日志模块 — AuditRecord + AuditStore

证券行业合规要求操作留痕。
每笔问答完整记录（问题/检索/回答/评分/耗时），以 JSONL 格式追加写入，
仅追加、不可删除、不可篡改。

用法:
    store = AuditStore()
    store.append(record)
    results = store.query(start="2024-01-01", keyword="清算", page=1, page_size=20)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AuditRecord:
    """单笔问答审计记录

    不可删除、不可篡改，仅可追加。
    """
    id: str = ""  # 唯一标识（由 timestamp + question[:20] 哈希生成）
    timestamp: str = ""
    session_id: str = ""
    question: str = ""
    answer: str = ""
    retrieval_time_ms: float = 0.0
    generation_time_ms: float = 0.0
    total_time_ms: float = 0.0
    from_standard_answer: bool = False
    match_type: str = ""
    faithfulness_result: str = ""
    faithfulness_score: float = 0.0
    sources: list[dict[str, Any]] = field(default_factory=list)
    api_error: str | None = None

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditRecord:
        return cls(
            timestamp=data.get("timestamp", ""),
            session_id=data.get("session_id", ""),
            question=data.get("question", ""),
            answer=data.get("answer", ""),
            retrieval_time_ms=data.get("retrieval_time_ms", 0.0),
            generation_time_ms=data.get("generation_time_ms", 0.0),
            total_time_ms=data.get("total_time_ms", 0.0),
            from_standard_answer=data.get("from_standard_answer", False),
            match_type=data.get("match_type", ""),
            faithfulness_result=data.get("faithfulness_result", ""),
            faithfulness_score=data.get("faithfulness_score", 0.0),
            sources=data.get("sources", []),
            api_error=data.get("api_error"),
        )


class AuditStore:
    """审计日志存储

    JSONL 格式（每行一条 JSON），仅追加写入。
    不支持删除或修改——满足监管"不可篡改"要求。

    存储路径: data/audit/audit.log
    """

    def __init__(self, store_path: str = "./data/audit"):
        self.store_path = Path(store_path)
        self.log_file = self.store_path / "audit.log"
        self._lock = threading.Lock()
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self.store_path.mkdir(parents=True, exist_ok=True)

    def append(self, record: AuditRecord) -> None:
        """追加一条审计记录（线程安全，追加写入）"""
        import hashlib
        if not record.timestamp:
            record.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not record.id:
            raw = f"{record.timestamp}_{record.question[:50]}"
            record.id = hashlib.sha256(raw.encode()).hexdigest()[:12]

        line = record.to_jsonl()
        with self._lock:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def query(
        self,
        start: str | None = None,
        end: str | None = None,
        keyword: str | None = None,
        session_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AuditRecord], int]:
        """查询审计日志

        Args:
            start: 起始时间 (>=)
            end: 结束时间 (<=)
            keyword: 关键词（在 question/answer 中匹配）
            session_id: 会话 ID
            page: 页码（从 1 开始）
            page_size: 每页条数

        Returns:
            (records, total_count)
        """
        # 快路径：无过滤条件时尾部读取（避免全量加载）
        has_filters = start or end or keyword or session_id
        if not has_filters and page == 1:
            records = self._iter_lines(from_tail=True, limit=page_size)
        else:
            # 慢路径：全量加载后过滤
            all_records = self._read_all()
            filtered = []
            for r in all_records:
                if start and r.timestamp < start:
                    continue
                if end and r.timestamp > end:
                    continue
                if session_id and session_id not in r.session_id:
                    continue
                if keyword:
                    kw = keyword.lower()
                    if kw not in r.question.lower() and kw not in (r.answer or "").lower():
                        continue
                filtered.append(r)
            filtered.sort(key=lambda r: r.timestamp, reverse=True)
            start_idx = (page - 1) * page_size
            records = filtered[start_idx:start_idx + page_size]

        return records, self._count_lines()

    def _count_lines(self) -> int:
        """高效行数统计（仅数换行符，不解析 JSON）"""
        if not self.log_file.exists():
            return 0
        count = 0
        with self._lock:
            with open(self.log_file, encoding="utf-8") as f:
                for _ in f:
                    count += 1
        return count

    def _read_all(self) -> list[AuditRecord]:
        """读取全部审计记录（逐行解析）"""
        if not self.log_file.exists():
            return []
        return list(self._iter_lines(from_tail=False))

    def _iter_lines(self, from_tail: bool = False, limit: int = 0) -> list[AuditRecord]:
        """迭代式读取 JSONL 行

        Args:
            from_tail: True 从尾部读取（最新优先）
            limit: 非 0 时限制条数
        """
        if not self.log_file.exists():
            return []

        records: list[AuditRecord] = []
        with self._lock:
            with open(self.log_file, encoding="utf-8") as f:
                if from_tail and limit:
                    # 从文件末尾读取最后 N 行
                    records = self._tail_lines(f, limit)
                else:
                    for line in f:
                        r = self._parse_line(line)
                        if r:
                            records.append(r)
                            if limit and len(records) >= limit:
                                break
        return records

    def _tail_lines(self, f, n: int) -> list[AuditRecord]:
        """从文件尾部读取最后 N 条有效记录"""
        f.seek(0, 2)  # 到文件末尾
        file_size = f.tell()
        buffer_size = 4096
        lines: list[str] = []
        pos = file_size

        while pos > 0 and len(lines) < n * 2:  # 多读一些应对空行/坏行
            read_size = min(buffer_size, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size)
            lines = chunk.splitlines(keepends=True) + lines

        records: list[AuditRecord] = []
        for line in reversed(lines):
            r = self._parse_line(line)
            if r:
                records.append(r)
                if len(records) >= n:
                    break
        return records

    def _parse_line(self, line: str) -> AuditRecord | None:
        line = line.strip()
        if not line:
            return None
        try:
            return AuditRecord.from_dict(json.loads(line))
        except json.JSONDecodeError:
            logger.warning(f"审计日志解析失败: {line[:80]}")
            return None

    def count(self) -> int:
        """总记录数"""
        return len(self._read_all())

    def get_stats(self) -> dict[str, Any]:
        """获取审计统计"""
        records = self._read_all()
        total = len(records)
        if total == 0:
            return {"total": 0, "earliest": "", "latest": "", "avg_time_ms": 0.0}
        avg_time = sum(r.total_time_ms for r in records if r.total_time_ms) / total
        return {
            "total": total,
            "earliest": records[0].timestamp if records else "",
            "latest": records[-1].timestamp if records else "",
            "avg_time_ms": round(avg_time, 1),
        }
