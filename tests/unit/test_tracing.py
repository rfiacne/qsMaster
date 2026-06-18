"""
Tracing 单元测试 — InMemoryMetrics + Tracer 降级
"""

from __future__ import annotations

import time
from unittest import mock

import pytest

from qa.pipelines.components.tracing import (
    InMemoryMetrics,
    Span,
    Tracer,
    get_metrics,
    get_tracer,
    init_tracing,
    reset_tracing,
)


class TestSpan:
    def test_create(self):
        s = Span("test")
        assert s.name == "test"
        assert s.attributes == {}

    def test_set_attribute(self):
        s = Span("test")
        s.set_attribute("key", "value")
        assert s.attributes["key"] == "value"

    def test_set_status(self):
        s = Span("test")
        s.set_status("error: timeout")
        assert s._status == "error: timeout"

    def test_duration(self):
        s = Span("test")
        time.sleep(0.01)
        s.end()
        assert s.duration_ms >= 8  # 至少 ~10ms

    def test_end_sets_duration(self):
        s = Span("test")
        s.end()
        assert s._end is not None


class TestTracer:
    def test_noop_by_default(self):
        t = Tracer()
        assert t.enabled is False

    def test_noop_span(self):
        t = Tracer()
        with t.start_span("test") as span:
            span.set_attribute("k", "v")
        assert span.attributes["k"] == "v"

    def test_otel_tracer_enabled(self):
        t = Tracer(otel_tracer=mock.MagicMock())
        assert t.enabled is True

    def test_span_context_manager_exception(self):
        t = Tracer()
        with pytest.raises(ValueError):
            with t.start_span("failing"):
                raise ValueError("test error")


class TestInMemoryMetrics:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.m = InMemoryMetrics()
        yield

    def test_empty_snapshot(self):
        s = self.m.snapshot()
        assert s.total_requests == 0
        assert s.latency_p50 == 0.0

    def test_record_one_request(self):
        self.m.record_request(100.0)
        s = self.m.snapshot()
        assert s.total_requests == 1
        assert s.latency_p50 == 100.0

    def test_record_multiple(self):
        for i in range(10):
            self.m.record_request(float(i * 10))
        s = self.m.snapshot()
        assert s.total_requests == 10
        assert s.avg_latency_ms == 45.0  # (0+10+...+90)/10

    def test_early_exit_counting(self):
        self.m.record_request(50.0, early_exit=True)
        self.m.record_request(200.0, early_exit=False)
        s = self.m.snapshot()
        assert s.early_exit_hits == 1

    def test_faithfulness_recording(self):
        self.m.record_faithfulness(passed=True)
        self.m.record_faithfulness(passed=True)
        self.m.record_faithfulness(passed=False)
        s = self.m.snapshot()
        assert s.faithfulness_passes == 2
        assert s.faithfulness_fails == 1

    def test_latency_percentiles(self):
        for i in range(100):
            self.m.record_request(float(i))  # 0..99 ms
        s = self.m.snapshot()
        assert s.latency_p50 >= 49  # median ~49.5
        assert s.latency_p50 <= 51
        assert s.latency_p95 >= 93
        assert s.latency_p99 >= 97

    def test_snapshot_to_dict(self):
        self.m.record_request(100.0)
        d = self.m.snapshot().to_dict()
        assert "total_requests" in d
        assert "early_exit_hit_rate" in d
        assert "latency_ms" in d
        assert "p50" in d["latency_ms"]

    def test_window_sliding(self):
        for i in range(2000):
            self.m.record_request(float(i % 100))
        self.m.snapshot()
        assert len(self.m._latencies) <= 1000


class TestInitTracing:
    def teardown_method(self):
        reset_tracing()

    def test_disabled_noop(self):
        t = init_tracing(enabled=False)
        assert t.enabled is False

    def test_no_otlp_endpoint(self):
        t = init_tracing(enabled=True, otlp_endpoint="")
        # 即使没有 OTel SDK 或端点，也不应崩溃
        assert t is not None

    def test_get_tracer_singleton(self):
        init_tracing(enabled=False)
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2

    def test_get_metrics_singleton(self):
        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2

    def test_reset(self):
        init_tracing(enabled=False)
        reset_tracing()
        # Should create new noop
        t = get_tracer()
        assert t is not None
