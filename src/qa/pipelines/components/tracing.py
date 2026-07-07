"""
OpenTelemetry 可观测性工具

为 RAG Pipeline 全链路提供 Span 追踪和 Metrics 采集。
支持降级：OTLP 不可达时自动切换到 NoopTracer，不阻塞主流程。

用法:
    from qa.pipelines.components.tracing import tracer, metrics

    with tracer.start_span("embedding") as span:
        span.set_attribute("dim", 1024)
        result = embed(text)

    metrics.record("qa.requests", 1)
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

Lock = threading.Lock

logger = logging.getLogger(__name__)


# ─── Metrics 采集器 ─────────────────────────────────────


@dataclass
class MetricsSnapshot:
    """Metrics 快照"""

    total_requests: int = 0
    early_exit_hits: int = 0
    faithfulness_passes: int = 0
    faithfulness_fails: int = 0
    faithfulness_skipped: int = 0
    latency_p50: float = 0.0
    latency_p95: float = 0.0
    latency_p99: float = 0.0
    avg_latency_ms: float = 0.0
    # 成本追踪字段
    total_cost: float = 0.0
    avg_cost_per_request: float = 0.0
    llm_call_count: int = 0
    embedding_call_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "early_exit_hit_rate": round(self.early_exit_hits / max(self.total_requests, 1), 4),
            "faithfulness_pass_rate": round(
                self.faithfulness_passes
                / max(self.faithfulness_passes + self.faithfulness_fails, 1),
                4,
            ),
            "faithfulness_skipped": self.faithfulness_skipped,
            "latency_ms": {
                "p50": round(self.latency_p50, 1),
                "p95": round(self.latency_p95, 1),
                "p99": round(self.latency_p99, 1),
                "avg": round(self.avg_latency_ms, 1),
            },
            "cost": {
                "total_cost": round(self.total_cost, 6),
                "avg_cost_per_request": round(self.avg_cost_per_request, 6),
            },
            "llm_call_count": self.llm_call_count,
            "embedding_call_count": self.embedding_call_count,
        }


class InMemoryMetrics:
    """内存 Metrics 采集器

    记录 QPS、延迟分布、命中率。线程安全。
    当 OTLP 不可用时作为降级方案。
    """

    def __init__(self, window_size: int = 1000):
        self.window_size = window_size
        self._latencies: list[float] = []
        self._total = 0
        self._early_exit_hits = 0
        self._faithfulness_passes = 0
        self._faithfulness_fails = 0
        self._faithfulness_skipped = 0
        self._lock = Lock()
        self._window_start = time.time()
        # 成本追踪
        self._total_cost = 0.0
        self._llm_calls = 0
        self._embedding_calls = 0

    def record_request(self, latency_ms: float, early_exit: bool = False) -> None:
        with self._lock:
            self._total += 1
            self._latencies.append(latency_ms)
            if early_exit:
                self._early_exit_hits += 1
            # 滑动窗口
            if len(self._latencies) > self.window_size:
                self._latencies = self._latencies[-self.window_size :]

    def record_faithfulness(self, passed: bool) -> None:
        with self._lock:
            if passed:
                self._faithfulness_passes += 1
            else:
                self._faithfulness_fails += 1

    def record_faithfulness_skipped(self) -> None:
        """记录 Faithfulness 因低风险场景被跳过（不计入通过/失败率）"""
        with self._lock:
            self._faithfulness_skipped += 1

    def record_llm_cost(self, cost: float) -> None:
        """记录 LLM 调用成本（美元）"""
        with self._lock:
            self._total_cost += cost
            self._llm_calls += 1

    def record_embedding_call(self) -> None:
        """记录嵌入调用次数"""
        with self._lock:
            self._embedding_calls += 1

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            total = self._total
            ee_hits = self._early_exit_hits
            fp = self._faithfulness_passes
            ff = self._faithfulness_fails
            fs = self._faithfulness_skipped
            latencies = sorted(self._latencies) if self._latencies else [0.0]
            total_cost = self._total_cost
            llm_calls = self._llm_calls
            embedding_calls = self._embedding_calls

        n = len(latencies)
        return MetricsSnapshot(
            total_requests=total,
            early_exit_hits=ee_hits,
            faithfulness_passes=fp,
            faithfulness_fails=ff,
            faithfulness_skipped=fs,
            latency_p50=latencies[min(n - 1, int(n * 0.5))],
            latency_p95=latencies[min(n - 1, int(n * 0.95))],
            latency_p99=latencies[min(n - 1, int(n * 0.99))],
            avg_latency_ms=sum(latencies) / max(n, 1),
            total_cost=total_cost,
            avg_cost_per_request=total_cost / max(total, 1),
            llm_call_count=llm_calls,
            embedding_call_count=embedding_calls,
        )

    @property
    def qps(self) -> float:
        elapsed = time.time() - self._window_start
        return self._total / max(elapsed, 1)


# ─── Tracer 抽象 ─────────────────────────────────────


class Span:
    """Span 抽象（兼容 OTel 和 Noop）"""

    def __init__(self, name: str, attributes: dict[str, Any] | None = None):
        self.name = name
        self.attributes = attributes or {}
        self._start = time.time()
        self._end: float | None = None
        self._status: str | None = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, status: str) -> None:
        self._status = status

    def end(self) -> None:
        self._end = time.time()
        elapsed = (self._end - self._start) * 1000
        logger.debug(f"Span [{self.name}] {elapsed:.0f}ms status={self._status}")

    @property
    def duration_ms(self) -> float:
        end = self._end or time.time()
        return (end - self._start) * 1000


class Tracer:
    """Tracer 抽象

    有 OTel 时使用真实 OTel Tracer，否则使用 Noop。
    """

    def __init__(self, otel_tracer=None):
        self._otel = otel_tracer

    @contextmanager
    def start_span(self, name: str, attributes: dict[str, Any] | None = None):
        span = Span(name, attributes)
        try:
            yield span
        except Exception as e:
            span.set_status(f"error: {e}")
            span.end()
            raise
        else:
            span.end()

    @property
    def enabled(self) -> bool:
        return self._otel is not None


# ─── 全局单例 ─────────────────────────────────────

_tracer: Tracer | None = None
_metrics: InMemoryMetrics | None = None
_initialized = False
_tracing_lock = threading.Lock()


def init_tracing(
    service_name: str = "securities-qa-agent",
    otlp_endpoint: str = "",
    enabled: bool = True,
) -> Tracer:
    """初始化 Tracer

    尝试加载 OpenTelemetry。加载失败或 disabled 时使用 Noop Tracer。
    使用双重检查锁定确保线程安全。
    """
    global _tracer, _metrics, _initialized

    if _initialized:
        return _tracer or _noop_tracer()

    with _tracing_lock:
        if _initialized:
            return _tracer or _noop_tracer()

        _metrics = InMemoryMetrics()

        if not enabled:
            logger.info("OpenTelemetry 已禁用，使用 Noop Tracer")
            _tracer = _noop_tracer()
            _initialized = True
            return _tracer

        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create({"service.name": service_name})
            provider = TracerProvider(resource=resource)

            if otlp_endpoint:
                exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")
                processor = BatchSpanProcessor(exporter)
                provider.add_span_processor(processor)
                logger.info(f"OTLP Trace 已配置: {otlp_endpoint}")
            else:
                logger.info("OTel SDK 已加载，无远端端点（本地 Span 记录）")

            otel_tracer = provider.get_tracer(service_name)
            _tracer = Tracer(otel_tracer=otel_tracer)
            _initialized = True
            return _tracer

        except ImportError:
            logger.warning("opentelemetry 未安装，使用 Noop Tracer")
            _tracer = _noop_tracer()
            _initialized = True
            return _tracer
        except Exception as e:
            logger.warning(f"OpenTelemetry 初始化失败 ({e})，使用 Noop Tracer")
            _tracer = _noop_tracer()
            _initialized = True
            return _tracer


def _noop_tracer() -> Tracer:
    return Tracer()


def get_tracer() -> Tracer:
    """获取全局 Tracer 实例（线程安全）"""
    global _tracer
    if _tracer is None:
        with _tracing_lock:
            if _tracer is None:
                _tracer = _noop_tracer()
    return _tracer


def get_metrics() -> InMemoryMetrics:
    """获取全局 Metrics 实例（线程安全）"""
    global _metrics
    if _metrics is None:
        with _tracing_lock:
            if _metrics is None:
                _metrics = InMemoryMetrics()
    return _metrics


def reset_tracing() -> None:
    """重置（测试用）"""
    global _tracer, _metrics, _initialized
    _tracer = None
    _metrics = None
    _initialized = False
