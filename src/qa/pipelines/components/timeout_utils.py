"""
超时工具 — 防止 PDF 解析等阻塞操作卡死整个进程

用 threading.Thread + join(timeout) 包装任意阻塞调用。
超时后抛出 TimeoutError，调用方捕获后跳过该文档。

用法:
    result = run_with_timeout(
        func=converter.run,
        timeout_sec=30,
        sources=[path]
    )
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class OperationTimeoutError(Exception):
    """操作超时"""
    pass


def run_with_timeout(
    func: Callable,
    timeout_sec: float = 30.0,
    args: tuple = (),
    kwargs: dict | None = None,
) -> Any:
    """在子线程中运行函数，超时则抛出 TimeoutError

    Args:
        func: 要执行的函数
        timeout_sec: 超时秒数
        args: 位置参数
        kwargs: 关键字参数

    Returns:
        函数返回值

    Raises:
        OperationTimeoutError: 超时
        原函数异常: 函数执行中的异常
    """
    if kwargs is None:
        kwargs = {}

    result = []
    exception = []

    def runner():
        try:
            res = func(*args, **kwargs)
            result.append(res)
        except Exception as e:
            exception.append(e)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)

    if thread.is_alive():
        # 超时——线程仍在运行
        logger.warning(f"操作超时 ({timeout_sec}s): {func.__name__}")
        raise OperationTimeoutError(f"操作超时 ({timeout_sec}s): {func.__name__}")

    if exception:
        raise exception[0]

    return result[0]


def safe_convert(
    converter: Any,
    file_path: str,
    timeout_sec: float = 30.0,
) -> Any | None:
    """安全的文档转换包装

    超时或失败时返回 None（调用方跳过该文档）。

    Args:
        converter: Haystack 转换器实例（有 run 方法）
        file_path: 文件路径
        timeout_sec: 每文档超时秒数

    Returns:
        converter.run() 结果，或 None（失败/超时）
    """
    try:
        return run_with_timeout(
            func=converter.run,
            timeout_sec=timeout_sec,
            kwargs={"sources": [file_path]},
        )
    except OperationTimeoutError:
        logger.warning(f"文档转换超时，跳过: {file_path}")
        return None
    except Exception as e:
        logger.warning(f"文档转换失败，跳过: {file_path} — {e}")
        return None
