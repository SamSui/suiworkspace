"""存储客户端公共契约。

每个存储客户端实现同一组生命周期与探活接口，使得：
- `StorageContainer` 可以统一 startup / shutdown / health；
- `/healthz` 无需知道具体存储类型；
- 后续增量新增存储（如对象存储）只需实现 `BaseStore`。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(slots=True)
class HealthResult:
    """一次探活结果。`latency_ms` 便于在 /healthz 上直接看出哪个依赖在拖慢启动。"""

    name: str
    ok: bool
    latency_ms: float
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 2),
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.extra:
            payload["extra"] = self.extra
        return payload


class BaseStore(ABC):
    """存储客户端基类。

    子类只需实现 `connect` / `close` / `_probe`；`health()` 统一负责计时与异常兜底，
    避免每个客户端各写一份 try/except。
    """

    name: ClassVar[str] = "store"

    def __init__(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @abstractmethod
    async def connect(self) -> None:
        """建立连接池 / 客户端。必须幂等。"""

    @abstractmethod
    async def close(self) -> None:
        """释放连接。必须幂等，且允许在未连接时调用。"""

    @abstractmethod
    async def _probe(self) -> tuple[str, dict[str, Any]]:
        """执行一次轻量探活，返回 (detail, extra)。失败请直接抛异常。"""

    async def health(self) -> HealthResult:
        start = time.perf_counter()
        try:
            detail, extra = await self._probe()
            return HealthResult(
                name=self.name,
                ok=True,
                latency_ms=(time.perf_counter() - start) * 1000,
                detail=detail,
                extra=extra,
            )
        except Exception as exc:  # noqa: BLE001 — 探活必须吞掉所有异常并降级为结果
            return HealthResult(
                name=self.name,
                ok=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                detail=f"{type(exc).__name__}: {exc}",
            )

    async def guarded(self, op: Callable[[], Awaitable[Any]]) -> Any:
        """未连接时直接抛 StorageUnavailable，避免调用方拿到晦涩的 None 错误。"""
        from core.exceptions import StorageUnavailable

        if not self._connected:
            raise StorageUnavailable(f"{self.name} 尚未连接，请先调用 connect()")
        return await op()
