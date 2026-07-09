"""
AuditLogger 单元测试
"""

from __future__ import annotations

import tempfile

import pytest

from qa.pipelines.components.audit_logger import AuditRecord, AuditStore


class TestAuditRecord:
    def test_default(self):
        r = AuditRecord()
        assert r.question == ""
        assert r.api_error is None

    def test_with_values(self):
        r = AuditRecord(
            question="测试问题",
            answer="测试回答",
            retrieval_time_ms=10.0,
            generation_time_ms=200.0,
            total_time_ms=210.0,
            from_standard_answer=True,
            match_type="exact",
        )
        assert r.question == "测试问题"
        assert r.total_time_ms == 210.0
        assert r.from_standard_answer is True

    def test_to_jsonl(self):
        r = AuditRecord(question="Q", answer="A", total_time_ms=100.0)
        line = r.to_jsonl()
        import json

        parsed = json.loads(line)
        assert parsed["question"] == "Q"
        assert parsed["total_time_ms"] == 100.0

    def test_from_dict_roundtrip(self):
        original = AuditRecord(  # noqa: F841 — used implicitly in assert
            question="Q",
            answer="A",
            total_time_ms=100.0,
            faithfulness_result="pass",
            faithfulness_score=0.9,
        )
        d = {
            "timestamp": "",
            "session_id": "",
            "question": "Q",
            "answer": "A",
            "retrieval_time_ms": 0.0,
            "generation_time_ms": 0.0,
            "total_time_ms": 100.0,
            "from_standard_answer": False,
            "match_type": "",
            "faithfulness_result": "pass",
            "faithfulness_score": 0.9,
            "sources": [],
            "api_error": None,
        }
        restored = AuditRecord.from_dict(d)
        assert restored.question == "Q"
        assert restored.faithfulness_score == 0.9


class TestAuditStore:
    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmpdir, AuditStore(store_path=tmpdir) as store:
            self.store = store
            yield

    def test_empty(self):
        assert self.store.count() == 0

    def test_append_one(self):
        r = AuditRecord(question="Q", answer="A")
        self.store.append(r)
        assert self.store.count() == 1

    def test_append_multiple(self):
        for i in range(10):
            self.store.append(AuditRecord(question=f"Q{i}", answer=f"A{i}"))
        assert self.store.count() == 10

    def test_query_all(self):
        self.store.append(AuditRecord(question="第一个问题", answer="回答1"))
        self.store.append(AuditRecord(question="第二个问题", answer="回答2"))
        records, total = self.store.query()
        assert total == 2
        assert len(records) == 2

    def test_query_pagination(self):
        for i in range(25):
            self.store.append(AuditRecord(question=f"Q{i}", answer=f"A{i}"))
        # 第一页
        page1, total = self.store.query(page=1, page_size=10)
        assert len(page1) == 10
        assert total == 25
        # 第三页
        page3, total = self.store.query(page=3, page_size=10)
        assert len(page3) == 5

    def test_query_by_keyword(self):
        self.store.append(AuditRecord(question="清算流程", answer="T+1"))
        self.store.append(AuditRecord(question="CCASS交收", answer="中央结算"))
        records, total = self.store.query(keyword="清算")
        assert total == 1
        assert records[0].question == "清算流程"

    def test_query_by_keyword_in_answer(self):
        self.store.append(AuditRecord(question="问题1", answer="清算规则说明"))
        records, total = self.store.query(keyword="清算规则")
        assert total == 1

    def test_query_by_time_range(self):
        self.store.append(AuditRecord(question="旧问题", timestamp="2024-01-01T00:00:00"))
        self.store.append(AuditRecord(question="新问题", timestamp="2024-06-01T00:00:00"))
        records, total = self.store.query(start="2024-05-01")
        assert total == 1
        assert records[0].question == "新问题"

        records, total = self.store.query(end="2024-02-01")
        assert total == 1
        assert records[0].question == "旧问题"

    def test_query_time_sort_descending(self):
        """结果应按时间倒序"""
        self.store.append(AuditRecord(question="最早", timestamp="2024-01-01T00:00:00"))
        self.store.append(AuditRecord(question="中间", timestamp="2024-03-01T00:00:00"))
        self.store.append(AuditRecord(question="最晚", timestamp="2024-06-01T00:00:00"))
        records, total = self.store.query()
        assert records[0].question == "最晚"
        assert records[-1].question == "最早"

    def test_append_only_not_deletable(self):
        """不应提供删除方法"""
        assert not hasattr(self.store, "delete")
        assert not hasattr(self.store, "clear")

    def test_persistence(self):
        """数据应持久化到磁盘"""
        r = AuditRecord(question="持久化测试", answer="答案")
        self.store.append(r)

        store2 = AuditStore(store_path=str(self.store.store_path))
        assert store2.count() == 1
        records, _ = store2.query()
        assert records[0].question == "持久化测试"

    def test_concurrent_append(self):
        """并发写入不丢数据"""
        import threading

        def writer(n):
            for i in range(20):
                self.store.append(AuditRecord(question=f"线程{n}-Q{i}", answer="A"))

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert self.store.count() == 60

    def test_get_stats_empty(self):
        stats = self.store.get_stats()
        assert stats["total"] == 0

    def test_get_stats_with_data(self):
        self.store.append(
            AuditRecord(
                question="Q",
                answer="A",
                total_time_ms=100.0,
                timestamp="2024-01-01T00:00:00",
            )
        )
        self.store.append(
            AuditRecord(
                question="Q2",
                answer="A2",
                total_time_ms=200.0,
                timestamp="2024-06-01T00:00:00",
            )
        )
        stats = self.store.get_stats()
        assert stats["total"] == 2
        assert stats["avg_time_ms"] == 150.0
        assert "2024-01-01" in stats["earliest"]
        assert "2024-06-01" in stats["latest"]

    def test_timestamp_auto_set(self):
        r = AuditRecord(question="Q", answer="A")
        assert r.timestamp == ""  # append 时设置
        self.store.append(r)
        # 重新读取
        records, _ = self.store.query()
        assert records[0].timestamp != ""

    def test_query_by_session_id(self):
        """M4 FR-014: 按 session_id 查询审计日志"""
        self.store.append(AuditRecord(question="Q1", answer="A1", session_id="session-abc"))
        self.store.append(AuditRecord(question="Q2", answer="A2", session_id="session-xyz"))
        self.store.append(AuditRecord(question="Q3", answer="A3", session_id="session-abc"))
        records, total = self.store.query(session_id="session-abc")
        assert total == 2
        assert all(r.session_id == "session-abc" for r in records)

    def test_query_combined_filters(self):
        """组合过滤条件：时间范围 + 关键词"""
        self.store.append(
            AuditRecord(question="清算流程", answer="T+1", timestamp="2024-06-01T10:00:00")
        )
        self.store.append(
            AuditRecord(question="CCASS交收", answer="中央结算", timestamp="2024-06-01T11:00:00")
        )
        records, total = self.store.query(start="2024-06-01", keyword="清算")
        assert total == 1
        assert records[0].question == "清算流程"

    def test_log_rotation(self):
        """超限触发日志轮转"""
        import shutil
        import tempfile

        _tmpdir = tempfile.mkdtemp()
        try:
            small_store = AuditStore(
                store_path=_tmpdir,
                max_bytes=200,  # 极小阈值触发轮转
                backup_count=3,
            )
            for i in range(20):
                small_store.append(AuditRecord(question=f"问题{i}", answer=f"回答{i}" * 5))
            # 主文件应存在
            assert small_store.log_file.exists()
            # 应有轮转备份
            backups = list(small_store.store_path.glob("audit.*.log"))
            assert len(backups) > 0
        finally:
            shutil.rmtree(_tmpdir, ignore_errors=True)

    def test_malformed_line_skipped(self):
        """损坏的 JSONL 行被跳过而非崩溃"""
        # 手动写入损坏数据
        with open(self.store.log_file, "a", encoding="utf-8") as f:
            f.write("this is not json\n")
            f.write('{"question": "valid", "answer": "A"}\n')
        records, total = self.store.query(keyword="valid")
        # 损坏行被跳过，仅返回有效记录
        assert len(records) == 1
        assert records[0].question == "valid"
