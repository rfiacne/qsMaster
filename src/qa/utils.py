"""
项目级公共工具函数 — 跨模块共享的无状态助手。
"""

from __future__ import annotations


def format_bytes(size: int) -> str:
    """格式化字节数为可读字符串"""
    sz: float = size
    for unit in ("B", "KB", "MB", "GB"):
        if sz < 1024:
            return f"{sz:.1f} {unit}"
        sz /= 1024
    return f"{sz:.1f} TB"


def short_name(path: str) -> str:
    """从完整路径中提取文件名"""
    if not path or path == "unknown":
        return "unknown"
    name = path.replace("\\", "/").split("/")[-1]
    # 如果文件名太长（含 UUID 前缀），截断保留后半段
    if len(name) > 50:
        parts = name.split("_", 2)
        if len(parts) >= 3:
            name = "_".join(parts[-2:])
    return name


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """两个等长向量的余弦相似度（使用 numpy 优化）"""
    if len(a) != len(b) or not a:
        return 0.0
    try:
        import numpy as np

        a_arr = np.asarray(a, dtype=float)
        b_arr = np.asarray(b, dtype=float)
        norm_a = np.linalg.norm(a_arr)
        norm_b = np.linalg.norm(b_arr)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))
    except ImportError:
        # numpy 不可用时回退到纯 Python 实现
        dot: float = sum(x * y for x, y in zip(a, b))
        norm_a: float = sum(x * x for x in a) ** 0.5
        norm_b: float = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
