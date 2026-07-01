"""
查询级 LRU 缓存 — 命中后直接返回 QueryResult

Key: (归一化问题, top_k, filters_hash)
失效策略:
- TTL 超时（默认 300s）
- LRU 淘汰（默认 256 条）
- 索引版本变更整体失效（监听 store_version）

检查点在 Early Exit 之后（标准答案优先于缓存）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

# 使用 forward reference 避免循环导入（querying.py ←→ query_cache.py）
# QueryResult 类型在方法签名中用 Any 替代，运行时 duck-typing

logger = logging.getLogger(__name__)


@dataclass
class QueryCacheEntry:
    """缓存条目"""

    key: str = ""
    result: Any | None = None  # QueryResult (avoid circular import)
    created_at: float = 0.0
    ttl: float = 300.0
    faithfulness_verified: bool = False  # 是否已通过 Faithfulness 校验
    faithfulness_score: float = 1.0  # 校验得分 (0~1)

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl


class QueryCache:
    """查询级 LRU 缓存

    用法:
        cache = QueryCache(max_size=256, ttl_seconds=300)
        cache.put(key, result)
        cached = cache.get(key)
        cache.invalidate_all()  # 索引版本变更时
    """

    def __init__(
        self,
        max_size: int = 256,
        ttl_seconds: float = 300.0,
        enabled: bool = True,
        store_manager: Any = None,
    ):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        self.store_manager = store_manager
        self._cache: OrderedDict[str, QueryCacheEntry] = OrderedDict()
        self._last_version: str = ""
        self._hit_count = 0
        self._miss_count = 0
        self._lock = threading.Lock()

    def _make_key(
        self,
        question: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> str:
        """生成缓存 key

        key = sha256(question + "|" + str(top_k) + "|" + filters_json)
        """
        filters_json = json.dumps(filters or {}, sort_keys=True)
        raw = f"{question}|{top_k}|{filters_json}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(
        self,
        question: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> Any | None:  # returns QueryResult or None
        """获取缓存条目

        Returns:
            QueryResult 或 None（未命中/过期）
        """
        if not self.enabled:
            return None

        key = self._make_key(question, top_k, filters)

        with self._lock:
            # 版本检查 + 失效在同一锁内原子完成，避免并发下重复清理
            if not self._version_valid_locked():
                logger.debug("查询缓存: 索引版本变更，整体失效")
                self._invalidate_all_locked()
                return None

            entry = self._cache.get(key)

            if entry is None:
                self._miss_count += 1
                return None

            if entry.expired:
                self._cache.pop(key, None)
                self._miss_count += 1
                return None

            # LRU: 移动到末尾（最近使用）
            self._cache.move_to_end(key)
            self._hit_count += 1
            logger.debug(f"查询缓存命中: key={key[:12]}...")
            return entry.result

    def put(
        self,
        question: str,
        top_k: int,
        result: Any,  # QueryResult
        filters: dict[str, Any] | None = None,
        faithfulness_verified: bool = False,
        faithfulness_score: float = 1.0,
    ) -> None:
        """写入缓存条目

        Args:
            question: 用户问题
            top_k: 检索数量
            result: QueryResult 对象
            filters: 过滤条件
            faithfulness_verified: 是否已通过 Faithfulness 校验
            faithfulness_score: 校验得分
        """
        if not self.enabled:
            return

        key = self._make_key(question, top_k, filters)

        with self._lock:
            # 仅在新增 key 时淘汰，覆盖已有 key 不应驱逐其他条目
            if key not in self._cache and len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)

            entry = QueryCacheEntry(
                key=key,
                result=result,
                created_at=time.time(),
                ttl=self.ttl_seconds,
                faithfulness_verified=faithfulness_verified,
                faithfulness_score=faithfulness_score,
            )
            self._cache[key] = entry
            logger.debug(f"查询缓存写入: key={key[:12]}...")

    def invalidate_all(self) -> None:
        """清空全部缓存"""
        with self._lock:
            self._invalidate_all_locked()

    def _invalidate_all_locked(self) -> None:
        """清空全部缓存（调用方需已持有 _lock）"""
        count = len(self._cache)
        self._cache.clear()
        self._last_version = self._get_current_version()
        if count > 0:
            logger.info(f"查询缓存已清空: {count} 条")

    def _version_valid(self) -> bool:
        """检查索引版本是否一致（线程安全包装）"""
        with self._lock:
            return self._version_valid_locked()

    def _version_valid_locked(self) -> bool:
        """检查索引版本是否一致（调用方需已持有 _lock）"""
        if self.store_manager is None:
            return True
        current = self._get_current_version()
        if not self._last_version:
            self._last_version = current
            return True
        return current == self._last_version

    def _get_current_version(self) -> str:
        """获取当前 store 版本号"""
        if self.store_manager is None:
            return ""
        try:
            return str(self.store_manager.store_version)
        except Exception:
            return ""

    @property
    def stats(self) -> dict[str, Any]:
        """缓存统计"""
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "hit_rate": (
                round(self._hit_count / (self._hit_count + self._miss_count), 4)
                if (self._hit_count + self._miss_count) > 0
                else 0.0
            ),
            "enabled": self.enabled,
        }


def create_query_cache(
    max_size: int = 256,
    ttl_seconds: float = 300.0,
    enabled: bool = True,
    store_manager: Any = None,
) -> QueryCache:
    """创建 QueryCache 实例"""
    return QueryCache(
        max_size=max_size,
        ttl_seconds=ttl_seconds,
        enabled=enabled,
        store_manager=store_manager,
    )
