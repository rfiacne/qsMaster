"""
QueryCache 线程安全与基本语义测试（M6 review finding #5）

验证：
- 并发 get/put 不抛 KeyError、不超 max_size
- LRU 覆盖已有 key 不驱逐其他条目
- 版本变更触发整体失效
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from qa.pipelines.components.query_cache import QueryCache


class FakeStore:
    """最小 store 替身，store_version 可控"""

    def __init__(self, version: str = "v1"):
        self._version = version

    @property
    def store_version(self):
        return self._version


def test_concurrent_get_put_no_errors():
    """多线程并发 get/put 不抛异常，最终大小不超过 max_size。"""
    cache = QueryCache(max_size=50, ttl_seconds=300, store_manager=FakeStore())

    def worker(i: int):
        for j in range(100):
            q = f"question-{i}-{j % 20}"
            cache.put(q, 5, result=f"r-{i}-{j}")
            cache.get(q, 5)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))

    with cache._lock:
        assert len(cache._cache) <= cache.max_size
    assert cache.stats["hit_count"] >= 0  # 未抛异常即通过


def test_concurrent_get_does_not_raise_keyerror_on_move_to_end():
    """一个线程 move_to_end 时另一线程 pop 同 key 不应抛 KeyError。"""
    cache = QueryCache(max_size=10, store_manager=FakeStore())
    cache.put("q1", 5, result="r1")

    errors: list[Exception] = []

    def getter():
        try:
            for _ in range(500):
                cache.get("q1", 5)
        except Exception as e:
            errors.append(e)

    def invalidator():
        try:
            for _ in range(500):
                cache.invalidate_all()
                cache.put("q1", 5, result="r1")
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=getter)
    t2 = threading.Thread(target=invalidator)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert errors == [], f"并发访问抛出异常: {errors}"


def test_put_overwrite_does_not_evict_other_key():
    """覆盖已有 key 时不应驱逐其他条目（finding #5 修复）。"""
    cache = QueryCache(max_size=2, store_manager=FakeStore())
    cache.put("a", 5, result="ra")
    cache.put("b", 5, result="rb")
    # 缓存已满（a, b）。覆盖 a 不应驱逐 b
    cache.put("a", 5, result="ra2")
    assert cache.get("b", 5) == "rb"
    assert cache.get("a", 5) == "ra2"


def test_version_change_invalidates():
    store = FakeStore(version="v1")
    cache = QueryCache(store_manager=store)
    cache.put("q", 5, result="r")
    assert cache.get("q", 5) == "r"
    store._version = "v2"
    assert cache.get("q", 5) is None  # 版本变更后失效


def test_expired_entry_returns_none():
    cache = QueryCache(ttl_seconds=0.01, store_manager=FakeStore())
    cache.put("q", 5, result="r")
    import time

    time.sleep(0.02)
    assert cache.get("q", 5) is None
