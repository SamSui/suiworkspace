"""Redis 客户端：会话缓存 / Checkpoint / 检索缓存 / 限流 / 任务队列 共用一个实例。

职责边界（设计文档 §2.6）：按 key 前缀隔离用途，全部带 TTL——避免内存膨胀。
"""

from __future__ import annotations

import time
from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool

from core.config import RedisSettings
from core.exceptions import StorageUnavailable
from core.logging import get_logger
from core.storage.base import BaseStore

logger = get_logger(__name__)

# 滑动窗口限流脚本：原子地 INCR + 首次设置过期，避免 INCR 与 EXPIRE 之间崩溃导致键永不过期
_SLIDING_WINDOW_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class RedisStore(BaseStore):
    name = "redis"

    def __init__(self, settings: RedisSettings) -> None:
        super().__init__()
        self._settings = settings
        self._pool: ConnectionPool | None = None
        self._client: Redis | None = None

    # ---------- 生命周期 ----------

    async def connect(self) -> None:
        if self._connected:
            return
        s = self._settings
        self._pool = ConnectionPool.from_url(
            s.url,
            max_connections=s.max_connections,
            socket_timeout=s.socket_timeout,
            socket_connect_timeout=s.socket_connect_timeout,
            decode_responses=True,
        )
        self._client = aioredis.Redis(connection_pool=self._pool)
        self._connected = True
        logger.info("redis store connected")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        if self._pool is not None:
            await self._pool.disconnect()
        self._client = None
        self._pool = None
        self._connected = False
        logger.info("redis store closed")

    # ---------- 探活 ----------

    async def _probe(self) -> tuple[str, dict[str, Any]]:
        client = self.client
        start = time.perf_counter()
        pong = await client.ping()
        latency = (time.perf_counter() - start) * 1000
        info = await client.info("memory")
        used = int(info.get("used_memory", 0))
        return (
            f"PING={pong}, used_memory={used / 1024 / 1024:.1f}MB, rtt={latency:.1f}ms",
            {"used_memory_bytes": used},
        )

    # ---------- 使用入口 ----------

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise StorageUnavailable("redis 尚未连接，请先调用 connect()")
        return self._client

    # ---------- 限流（设计文档 §9.1：Redis 滑动窗口）----------

    async def hit_rate_limit(self, identity: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """返回 (是否放行, 当前窗口计数)。按 identity（user_id / IP）分组。"""
        key = f"{self._settings.prefix_rate_limit}{identity}:{window_seconds}"
        current = int(await self.client.eval(_SLIDING_WINDOW_LUA, 1, key, window_seconds))
        return current <= limit, current

    # ---------- 分布式锁 ----------

    async def acquire_lock(self, resource: str, *, ttl_seconds: int = 30) -> str | None:
        """SET NX EX 获取锁，返回 token；未获取到返回 None。释放请用 release_lock。"""
        import uuid

        token = uuid.uuid4().hex
        key = f"{self._settings.prefix_lock}{resource}"
        ok = await self.client.set(key, token, nx=True, ex=ttl_seconds)
        return token if ok else None

    async def release_lock(self, resource: str, token: str) -> bool:
        """仅释放自己持有的锁（Lua 保证 check-and-delete 原子）。"""
        key = f"{self._settings.prefix_lock}{resource}"
        script = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]) else return 0 end"
        )
        return bool(await self.client.eval(script, 1, key, token))

    # ---------- 检索缓存（设计文档 §4）----------

    async def get_json(self, key: str) -> Any | None:
        import json

        raw = await self.client.get(key)
        return json.loads(raw) if raw else None

    async def set_json(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        import json

        await self.client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)

    async def invalidate_prefix(self, prefix: str) -> int:
        """按前缀失效（文档重新摄入后清理检索缓存）。用 SCAN 而非 KEYS，避免阻塞。"""
        removed = 0
        async for key in self.client.scan_iter(match=f"{prefix}*", count=500):
            removed += int(await self.client.delete(key))
        return removed
