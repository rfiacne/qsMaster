"""
超时工具单元测试 — timeout_utils.py

覆盖:
  - run_with_timeout: 正常完成、超时抛出 OperationTimeoutError、异常传播
  - safe_convert: 超时返回 None、异常返回 None、正常返回结果
"""

from __future__ import annotations

import time
from unittest import mock

import pytest

from qa.pipelines.components.timeout_utils import (
    OperationTimeoutError,
    run_with_timeout,
    safe_convert,
)


class TestRunWithTimeout:
    def test_normal_completion(self):
        """函数在超时内正常完成，返回结果"""
        result = run_with_timeout(lambda: 42, timeout_sec=5.0)
        assert result == 42

    def test_normal_completion_with_args(self):
        """带参数的函数正常完成"""
        result = run_with_timeout(lambda a, b: a + b, timeout_sec=5.0, args=(3, 4))
        assert result == 7

    def test_normal_completion_with_kwargs(self):
        """带关键字参数的函数正常完成"""
        result = run_with_timeout(
            lambda x, y=10: x + y, timeout_sec=5.0, args=(5,), kwargs={"y": 20}
        )
        assert result == 25

    def test_timeout_raises_operation_timeout_error(self):
        """超时抛出 OperationTimeoutError"""

        def slow_func():
            time.sleep(10)
            return "done"

        with pytest.raises(OperationTimeoutError, match="超时"):
            run_with_timeout(slow_func, timeout_sec=0.1)

    def test_exception_propagation(self):
        """函数内部异常原样抛出"""

        def failing_func():
            raise ValueError("测试异常")

        with pytest.raises(ValueError, match="测试异常"):
            run_with_timeout(failing_func, timeout_sec=5.0)

    def test_return_none(self):
        """函数返回 None 时正常处理"""
        result = run_with_timeout(lambda: None, timeout_sec=5.0)
        assert result is None


class TestSafeConvert:
    def test_normal_conversion(self):
        """正常转换返回结果"""
        converter = mock.MagicMock()
        converter.run.return_value = {"documents": ["doc1"]}
        converter.run.__name__ = "run"

        result = safe_convert(converter, "/path/to/file.pdf", timeout_sec=5.0)
        assert result == {"documents": ["doc1"]}

    def test_timeout_returns_none(self):
        """超时时返回 None"""
        converter = mock.MagicMock()
        converter.run.side_effect = lambda **kwargs: time.sleep(10)
        converter.run.__name__ = "run"

        result = safe_convert(converter, "/path/to/file.pdf", timeout_sec=0.1)
        assert result is None

    def test_exception_returns_none(self):
        """转换异常时返回 None"""
        converter = mock.MagicMock()
        converter.run.side_effect = RuntimeError("解析失败")
        converter.run.__name__ = "run"

        result = safe_convert(converter, "/path/to/file.pdf", timeout_sec=5.0)
        assert result is None
