"""配额（P1.3 · SUIG-33）。

面向“调用方 key”的分钟窗口配额：`tokens_per_minute` 与 `calls_per_minute`。
实现为按 key 的 60s 可变窗口计数（线程安全、无后台轮询）。

后端可插拔：`MemoryQuotaBackend`（单测 / 本机）与 Redis 可在同一
`PolicyQuotaManager` 内切换（多实例一致性走 Redis，生产优先）。
"""

from __future__ import annotations

import threading
import time

from core.logging import get_logger

from .errors import QuotaExceeded

logger = get_logger(__name__)


class QuotaPolicy:
    """一次配额策略：窗口固定 60s，允许的 tokens/calls。0 表示不限制该项。"""

    __slots__ = ("key", "tokens_per_minute", "calls_per_minute")

    def __init__(self, *, key: str, tokens_per_minute: int, calls_per_minute: int) -> None:
        self.key = key
        self.tokens_per_minute = tokens_per_minute
        self.calls_per_minute = calls_per_minute


class QuotaBackend:
    """配额计数后端抽象（线程安全）。窗口 60s。"""

    def within(self, key: str) -> float:
        """返回「窗口重置剩余秒数」，用于 retry_after。"""
        raise NotImplementedError  # pragma: no cover — 抽象方法

    def sample(self, key: str) -> tuple[int, int]:
        """返回 (本窗口已用 tokens, 本窗口已用 calls)。"""
        raise NotImplementedError  # pragma: no cover — 抽象方法

    def consume(self, key: str, tokens: int) -> None:
        """把 tokens 计入本窗口（调用方已判定允许）。"""
        raise NotImplementedError  # pragma: no cover — 抽象方法


class MemoryQuotaBackend(QuotaBackend):
    """进程内可变窗口后端。单测 / 单实例可无 Redis 直接跑。线程安全。"""

    _WINDOW = 60.0

    def __init__(self) -> None:
        self._tokens: dict[str, list[float]] = {}
        self._calls: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._window_start: dict[str, float] = {}

    def within(self, key: str) -> float:
        with self._lock:
            start = self._window_start.get(key, time.monotonic())
            return max(0.0, self._WINDOW - (self._now() - start))

    def sample(self, key: str) -> tuple[int, int]:
        now = self._now()
        cut = now - self._WINDOW
        with self._lock:
            toks = [t for t in self._tokens.get(key, []) if t >= cut]
            calls = [c for c in self._calls.get(key, []) if c >= cut]
        return len(toks), len(calls)

    def consume(self, key: str, tokens: int) -> None:
        now = self._now()
        cut = now - self._WINDOW
        with self._lock:
            self._window_start.setdefault(key, now)
            self._tokens[key] = [t for t in self._tokens.get(key, []) if t >= cut] + [now] * tokens
            self._calls[key] = [c for c in self._calls.get(key, []) if c >= cut] + [now]

    def _now(self) -> float:
        return time.monotonic()


class PolicyQuotaManager:
    """聚合：一套配额策略 + 一个后端，统一 `enforce` / `snapshot`。"""

    def __init__(self, policies: list[QuotaPolicy], backend: QuotaBackend) -> None:
        self._by_key = {p.key: p for p in policies}
        self._backend = backend

    @property
    def backend(self) -> QuotaBackend:
        return self._backend

    def policy_for(self, key: str) -> QuotaPolicy | None:
        return self._by_key.get(key)

    def check(self, key: str, tokens: int, *, bump: bool = False) -> tuple[bool, float]:
        """判定 key 消耗 tokens+1 次调用后是否允许；`bump=True` 时同步消耗。

        返回 (是否允许, retry_after_seconds)。
        """
        ok, retry = self._allows(key, tokens)
        if bump and ok:
            self._backend.consume(key, tokens)
        return ok, retry

    def _allows(self, key: str, tokens: int) -> tuple[bool, float]:
        """不消耗下判定 key 消耗 tokens+1 次调用后是否允许。返回 (允许?, retry_after)。"""
        policy = self._by_key.get(key)
        if policy is None:
            return True, 0.0
        toks_i, calls_i = self._backend.sample(key)
        allowed = True
        if policy.tokens_per_minute:
            allowed = allowed and (toks_i + tokens <= policy.tokens_per_minute)
        if policy.calls_per_minute:
            allowed = allowed and (calls_i + 1 <= policy.calls_per_minute)
        if not allowed:
            return False, self._backend.within(key)
        return True, 0.0

    def enforce(self, key: str, tokens: int) -> None:
        """判定并消耗；不允许即抛 QuotaExceeded（429）。"""
        ok, retry = self._allows(key, tokens)
        if not ok:
            raise QuotaExceeded(
                f"配额不足: key={key}", detail={"key": key, "tokens": tokens}, retry_after=retry
            )
        # 消耗 token + 1 次调用
        self._backend.consume(key, tokens)

    def snapshot(self, key: str) -> dict:
        policy = self._by_key.get(key)
        toks_i, calls_i = self._backend.sample(key)
        return {
            "key": key,
            "tokens_per_minute": policy.tokens_per_minute if policy else 0,
            "calls_per_minute": policy.calls_per_minute if policy else 0,
            "tokens_used_this_minute": toks_i,
            "calls_used_this_minute": calls_i,
        }


__all__ = ["QuotaPolicy", "MemoryQuotaBackend", "PolicyQuotaManager"]