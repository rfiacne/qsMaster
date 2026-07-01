"""
LLM API 熔断器 — 防止级联故障与成本失控

状态机:
  CLOSED   → 正常态，请求正常通过
  OPEN     → 熔断态，拒绝请求并触发降级
  HALF_OPEN→ 半开态，限流试探恢复

触发条件:
  - 连续失败次数 >= failure_threshold
  - 单请求成本超过 cost_limit_per_request

恢复策略:
  - OPEN 状态持续 reset_timeout 秒后自动进入 HALF_OPEN
  - HALF_OPEN 状态下成功一次则恢复为 CLOSED

用法:
    breaker = CircuitBreaker(failure_threshold=5, reset_timeout=60.0)
    try:
        result = breaker.call(expensive_llm_api)
    except CircuitBreakerOpen:
        result = fallback()
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class BreakerState(enum.Enum):
    """熔断器状态"""

    CLOSED = "closed"  # 正常态
    OPEN = "open"  # 熔断态
    HALF_OPEN = "half_open"  # 半开态


class CircuitBreakerOpen(Exception):
    """熔断器已开启，请求被拒绝"""

    def __init__(self, message: str = "熔断器开启，拒绝请求以防止成本失控", state: str = "open"):
        super().__init__(message)
        self.state = state


@dataclass
class CircuitBreakerStats:
    """熔断器统计信息"""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0  # 熔断器拒绝（未尝试）
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    state_changes: int = 0
    total_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "rejected_calls": self.rejected_calls,
            "consecutive_failures": self.consecutive_failures,
            "state_changes": self.state_changes,
            "total_cost": round(self.total_cost, 4),
            "last_failure_time": self.last_failure_time,
        }


class CircuitBreaker:
    """LLM API 熔断器

    线程安全：所有状态变更在 _lock 保护下执行。

    属性:
        failure_threshold: 连续失败触发熔断的阈值
        reset_timeout: 从 OPEN 到 HALF_OPEN 的等待时间（秒）
        cost_limit_per_request: 单请求成本上限（美元）
        cost_limit_per_minute: 每分钟成本上限（美元）
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
        cost_limit_per_request: float = 0.50,
        cost_limit_per_minute: float = 10.0,
    ):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.cost_limit_per_request = cost_limit_per_request
        self.cost_limit_per_minute = cost_limit_per_minute

        self._state = BreakerState.CLOSED
        self._lock = threading.Lock()
        self._stats = CircuitBreakerStats()
        # 每分钟成本追踪：[(timestamp, cost), ...]
        self._cost_history: list[tuple[float, float]] = []
        # 降级回调（可选）
        self._on_open: Callable[[], None] | None = None
        self._on_close: Callable[[], None] | None = None

    @property
    def state(self) -> BreakerState:
        """当前状态（线程安全读取）"""
        with self._lock:
            return self._state

    @property
    def stats(self) -> CircuitBreakerStats:
        """统计信息快照"""
        with self._lock:
            return CircuitBreakerStats(
                total_calls=self._stats.total_calls,
                successful_calls=self._stats.successful_calls,
                failed_calls=self._stats.failed_calls,
                rejected_calls=self._stats.rejected_calls,
                consecutive_failures=self._stats.consecutive_failures,
                last_failure_time=self._stats.last_failure_time,
                state_changes=self._stats.state_changes,
                total_cost=self._stats.total_cost,
            )

    def call(
        self,
        func: Callable[..., Any],
        *args: Any,
        cost: float = 0.0,
        fallback: Callable[..., Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """通过熔断器调用函数

        Args:
            func: 要调用的函数
            *args: 函数参数
            cost: 本次调用的预估成本（美元）。调用前检查是否超限。
            fallback: 熔断或超限时调用的降级函数
            **kwargs: 函数关键字参数

        Returns:
            函数返回值，或降级结果

        Raises:
            CircuitBreakerOpen: 熔断器开启且无 fallback
        """
        with self._lock:
            self._stats.total_calls += 1

            # 检查熔断器状态
            if not self._can_execute_locked():
                self._stats.rejected_calls += 1
                state_str = self._state.value
                logger.warning(
                    f"熔断器拒绝请求 (state={state_str}, "
                    f"consecutive_failures={self._stats.consecutive_failures})"
                )
                if fallback is not None:
                    return fallback(*args, **kwargs)
                raise CircuitBreakerOpen(state=state_str)

            # 检查单请求成本上限
            if cost > self.cost_limit_per_request:
                logger.error(f"单请求成本超限: ${cost:.4f} > ${self.cost_limit_per_request:.4f}")
                # 触发熔断（视为失败）
                self._record_failure_locked()
                if fallback is not None:
                    return fallback(*args, **kwargs)
                raise CircuitBreakerOpen(
                    message=f"单请求成本 ${cost:.4f} 超过上限 ${self.cost_limit_per_request:.4f}",
                    state=self._state.value,
                )

            # 检查每分钟成本上限
            if not self._check_minute_cost_locked(cost):
                logger.error(f"每分钟成本超限: 累计超出 ${self.cost_limit_per_minute:.4f}/min")
                self._record_failure_locked()
                if fallback is not None:
                    return fallback(*args, **kwargs)
                raise CircuitBreakerOpen(
                    message=f"每分钟成本上限 ${self.cost_limit_per_minute:.4f} 已耗尽",
                    state=self._state.value,
                )

        # 在锁外执行实际调用（避免长时间持锁）
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            with self._lock:
                self._record_failure_locked()
                self._stats.failed_calls += 1
            logger.warning(f"熔断器记录失败: {e}")
            raise
        else:
            with self._lock:
                self._record_success_locked()
                self._record_cost_locked(cost)
                self._stats.successful_calls += 1
            return result

    def record_cost(self, cost: float) -> None:
        """在调用完成后记录实际成本（用于无法预知成本的场景）

        如果成本超过单请求上限，触发熔断。
        """
        with self._lock:
            if cost > self.cost_limit_per_request:
                logger.error(
                    f"实际成本超限: ${cost:.4f} > ${self.cost_limit_per_request:.4f}，触发熔断"
                )
                self._record_failure_locked()
            else:
                self._record_cost_locked(cost)

    def reset(self) -> None:
        """手动重置熔断器（管理接口）"""
        with self._lock:
            self._state = BreakerState.CLOSED
            self._stats.consecutive_failures = 0
            self._stats.state_changes += 1
            self._cost_history.clear()
            logger.info("熔断器已手动重置")

    # ─── 内部状态管理（调用方需持有 _lock）────────────────────

    def _can_execute_locked(self) -> bool:
        """判断当前状态是否允许执行（调用方需持有 _lock）"""
        if self._state == BreakerState.CLOSED:
            return True
        if self._state == BreakerState.OPEN:
            # 检查是否可以进入半开态
            elapsed = time.time() - self._stats.last_failure_time
            if elapsed >= self.reset_timeout:
                self._state = BreakerState.HALF_OPEN
                self._stats.state_changes += 1
                logger.info(
                    f"熔断器: OPEN → HALF_OPEN (等待 {elapsed:.1f}s >= {self.reset_timeout}s)"
                )
                return True
            return False
        # HALF_OPEN: 允许一次试探
        return True

    def _record_success_locked(self) -> None:
        """记录成功（调用方需持有 _lock）"""
        self._stats.consecutive_failures = 0
        if self._state == BreakerState.HALF_OPEN:
            self._state = BreakerState.CLOSED
            self._stats.state_changes += 1
            logger.info("熔断器: HALF_OPEN → CLOSED（试探成功，恢复正常）")

    def _record_failure_locked(self) -> None:
        """记录失败（调用方需持有 _lock）"""
        self._stats.consecutive_failures += 1
        self._stats.last_failure_time = time.time()

        if self._state == BreakerState.HALF_OPEN:
            # 半开态失败：立即恢复熔断
            self._state = BreakerState.OPEN
            self._stats.state_changes += 1
            logger.warning("熔断器: HALF_OPEN → OPEN（试探失败，重新熔断）")
        elif (
            self._state == BreakerState.CLOSED
            and self._stats.consecutive_failures >= self.failure_threshold
        ):
            self._state = BreakerState.OPEN
            self._stats.state_changes += 1
            logger.warning(
                f"熔断器: CLOSED → OPEN "
                f"(连续失败 {self._stats.consecutive_failures} >= {self.failure_threshold})"
            )

    def _record_cost_locked(self, cost: float) -> None:
        """记录成本（调用方需持有 _lock）"""
        self._stats.total_cost += cost
        now = time.time()
        self._cost_history.append((now, cost))
        # 清理 60 秒前的记录
        self._cost_history = [(ts, c) for ts, c in self._cost_history if now - ts < 60.0]

    def _check_minute_cost_locked(self, additional_cost: float) -> bool:
        """检查加上本次调用后是否超出每分钟成本上限"""
        now = time.time()
        # 清理 60 秒前的记录
        self._cost_history = [(ts, c) for ts, c in self._cost_history if now - ts < 60.0]
        current_minute_cost = sum(c for _, c in self._cost_history)
        return (current_minute_cost + additional_cost) <= self.cost_limit_per_minute


# ─── 全局熔断器单例（每个 LLM 服务一个）────────────────────

_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_breaker(
    name: str = "llm_default",
    failure_threshold: int = 5,
    reset_timeout: float = 60.0,
    cost_limit_per_request: float = 0.50,
    cost_limit_per_minute: float = 10.0,
) -> CircuitBreaker:
    """获取或创建命名熔断器

    Args:
        name: 熔断器名称（如 "llm_generation", "faithfulness", "query_rewrite"）
        failure_threshold: 连续失败触发阈值
        reset_timeout: 恢复等待时间（秒）
        cost_limit_per_request: 单请求成本上限（美元）
        cost_limit_per_minute: 每分钟成本上限（美元）

    Returns:
        CircuitBreaker 实例
    """
    with _breakers_lock:
        if name not in _breakers:
            _breakers[name] = CircuitBreaker(
                failure_threshold=failure_threshold,
                reset_timeout=reset_timeout,
                cost_limit_per_request=cost_limit_per_request,
                cost_limit_per_minute=cost_limit_per_minute,
            )
            logger.info(
                f"创建熔断器 [{name}]: "
                f"threshold={failure_threshold}, "
                f"reset={reset_timeout}s, "
                f"max_cost_req=${cost_limit_per_request:.4f}, "
                f"max_cost_min=${cost_limit_per_minute:.4f}"
            )
        return _breakers[name]


def reset_all_breakers() -> None:
    """重置所有熔断器（测试/管理接口）"""
    with _breakers_lock:
        for breaker in _breakers.values():
            breaker.reset()
        _breakers.clear()
