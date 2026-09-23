"""聚合健康探活：把四类存储的探活结果汇成一个 /healthz 响应。

并发探活——任一存储慢不会串行拖慢整体；总耗时约等于最慢的那个依赖。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from core.storage.base import HealthResult

if TYPE_CHECKING:
    from core.storage.container import StorageContainer

# 探活总超时：超过则整体判不健康，避免 /healthz 被某个 hang 住的依赖拖死
PROBE_TIMEOUT_SECONDS = 5.0


async def check_all(container: StorageContainer) -> tuple[bool, list[HealthResult], dict[str, Any]]:
    """返回 (全部健康?, 各存储结果, 汇总信息)。"""
    stores = container.all()
    results = await asyncio.gather(
        *(asyncio.wait_for(store.health(), timeout=PROBE_TIMEOUT_SECONDS) for store in stores),
        return_exceptions=True,
    )

    health: list[HealthResult] = []
    for store, result in zip(stores, results, strict=True):
        if isinstance(result, HealthResult):
            health.append(result)
        else:
            # wait_for 超时或探活本身崩溃
            health.append(
                HealthResult(
                    name=store.name,
                    ok=False,
                    latency_ms=PROBE_TIMEOUT_SECONDS * 1000,
                    detail=f"{type(result).__name__}: {result}",
                )
            )

    healthy = all(item.ok for item in health)
    summary = {
        "healthy": healthy,
        "checked": len(health),
        "failed": [item.name for item in health if not item.ok],
        "stores": [item.as_dict() for item in health],
    }
    return healthy, health, summary
