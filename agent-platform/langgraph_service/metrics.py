"""编排服务埋点（增量 3）。

轻量本地计数器/直方图，供进程内观测与压测断言（3.7 P99）。不做 Prometheus 直出——
导出对接属增量 5（可观测）。约定：
- `record(name, value_ms)` 记直方图；`increment(name, 1)` 记计数。
- `latency_p(name, p)` 取某指标 p 分位（P50/P99），用于验收断言。
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock

_hist: dict[str, list[float]] = defaultdict(list)
_counts: dict[str, int] = defaultdict(int)
_lock = Lock()


def record(name: str, value_ms: float) -> None:
    with _lock:
        _hist[name].append(float(value_ms))


def bump(name: str, by: int = 1) -> None:
    with _lock:
        _counts[name] += by


def reset() -> None:
    with _lock:
        _hist.clear()
        _counts.clear()


def latency_p(name: str, p: float) -> float:
    """某指标 p 分位（0~100）。无数据返回 0。"""
    with _lock:
        samples = _hist.get(name, [])
        if not samples:
            return 0.0
        order = sorted(samples)
        idx = min(len(order) - 1, int(len(order) * p / 100))
        return order[idx]


def count(name: str) -> int:
    with _lock:
        return _counts.get(name, 0)


def snapshot() -> dict[str, object]:
    with _lock:
        return {
            "hist": {k: list(v) for k, v in _hist.items()},
            "counts": dict(_counts),
        }


def has_metric(name: str) -> bool:
    with _lock:
        return name in _hist