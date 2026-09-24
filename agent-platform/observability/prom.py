"""Prometheus 指标 + 既有埋点桥接（增量 5.4）。

一组语义清晰、命名带 `kb_` 前缀的进程内指标，`generate_latest()` 按 Prometheus
文本协议导出，供 `deploy/prometheus/prometheus.yml` scrape 各进程 `/metrics`。

覆盖验收 5.4 三件事：
- 缓存命中率：`kb_cache_hits_total` / `kb_cache_misses_total`；
- 检索 P99： `kb_retrieval_seconds` 直方图（配合 `p99()` 取 P99）；
- 错误率：`kb_errors_total{phase}`（网关 / 编排 / 存储层均可打点）；
另附依赖耗时 `kb_dependency_seconds{target,name}` 与请求量。

依赖策略：`prometheus-client` 在 `observability` extra；未安装时全部走 no-op
（计数 0、直方图空），调用方不报错——满足「未验证第三方契约不引入」。
"""

from __future__ import annotations

import threading
from typing import Any

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

    _PROM_OK = True
except Exception:  # pragma: no cover — observability extra 未启用
    _PROM_OK = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"

    class _Dummy:
        def labels(self, **kw: Any) -> _Dummy:
            return self

        def inc(self, *a: Any, **k: Any) -> None:
            pass

        def observe(self, *a: Any, **k: Any) -> None:
            pass

        def set(self, *a: Any, **k: Any) -> None:
            pass

        def inc_by(self, *a: Any, **k: Any) -> None:
            pass

        def observe_time(self) -> None:
            pass

    Counter = Gauge = Histogram = _Dummy


class Metrics:
    """进程内语义指标统一门面。线程安全；命名前缀 `kb_`。"""

    def __init__(self) -> None:
        self.enabled = _PROM_OK
        # 缓存命中率（5.4 验收 #1）
        self.cache_hits = Counter("kb_cache_hits_total", "检索缓存命中次数")
        self.cache_misses = Counter("kb_cache_misses_total", "检索缓存未命中次数")
        # 错误率（5.4 验收 #3）
        self.errors = Counter("kb_errors_total", "按阶段计的错误次数", ["phase"])
        # 检索与依赖耗时（秒）——P99 由此取（5.4 验收 #2）
        self.retrieval_seconds = Histogram(
            "kb_retrieval_seconds",
            "一次混合检索总耗时（秒）",
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        )
        self.dependency_seconds = Histogram(
            "kb_dependency_seconds",
            "依赖耗时（秒）",
            ("target", "name"),
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        )
        # 请求量 + 进行中（错误率分母 / HPA QPS 参考）
        self.requests_total = Counter("kb_requests_total", "HTTP 请求总数", ["route"])
        self.requests_inflight = Gauge("kb_requests_inflight", "进行中的请求数")

        # 进程内 P99 样本（不依赖 prometheus_client 也能断言，供测试/报告）
        self._p99: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        # 与 prometheus 计数器镜像的进程内计数（供测试/报告；prometheus 计数无法复位）
        self._counters: dict[str, int] = {
            "cache_hits": 0,
            "cache_misses": 0,
            "errors": 0,
        }

    # ---------------- 业务打点 ----------------
    def cache_hit(self) -> None:
        self._inc("cache_hits")
        if self.enabled:
            self.cache_hits.inc()

    def cache_miss(self) -> None:
        self._inc("cache_misses")
        if self.enabled:
            self.cache_misses.inc()

    def error(self, phase: str) -> None:
        with self._lock:
            self._counters["errors"] += 1
        if self.enabled:
            self.errors.labels(phase=phase).inc()

    def _inc(self, key: str) -> None:
        with self._lock:
            self._counters[key] += 1

    def request(self, route: str) -> None:
        if self.enabled:
            self.requests_total.labels(route=route).inc()

    def observe_retrieval(self, seconds: float) -> None:
        if self.enabled:
            self.retrieval_seconds.observe(seconds)
        self._record_p99("retrieval", seconds)

    def observe_dependency(self, target: str, name: str, seconds: float) -> None:
        if self.enabled:
            self.dependency_seconds.labels(target=target, name=name).observe(seconds)
        self._record_p99(f"dependency:{target}:{name}", seconds)

    # ---------------- P99 / 快照（供校验与报告，不需要 prometheus 也可用） ----------------
    def _record_p99(self, key: str, seconds: float) -> None:
        with self._lock:
            self._p99.setdefault(key, []).append(float(seconds))

    def p99_seconds(self, key: str = "retrieval") -> float:
        with self._lock:
            samples = self._p99.get(key)
            if not samples:
                return 0.0
            order = sorted(samples)
            return order[min(len(order) - 1, int(len(order) * 0.99))]

    def p50_seconds(self, key: str = "retrieval") -> float:
        with self._lock:
            samples = self._p99.get(key)
            if not samples:
                return 0.0
            order = sorted(samples)
            return order[min(len(order) - 1, int(len(order) * 0.50))]

    def clear(self) -> None:
        with self._lock:
            self._p99.clear()
            self._counters = {k: 0 for k in self._counters}

    def counts(self) -> dict[str, int]:
        """进程内镜像计数（供测试/报告，可复位；prometheus 计数不可复位）。"""
        with self._lock:
            return dict(self._counters)

    # ---------------- Prometheus 文本导出 ----------------
    def generate_latest(self) -> tuple[bytes, str]:
        """返回 (payload_bytes, content_type)。未装 prometheus_client 时给空 body。"""
        if not self.enabled:
            return (b"", CONTENT_TYPE_LATEST)
        _bridge_in_process_metrics()
        return (generate_latest(), CONTENT_TYPE_LATEST)


# 桥接直方图：把既有 langgraph_service.metrics（增量 3）的进程内直方图并进 Prometheus。
_inproc_hist: Any = None


def _ensure_inproc_hist():
    global _inproc_hist
    if not _PROM_OK:
        return None
    if _inproc_hist is None:
        _inproc_hist = Histogram(
            "kb_inproc_metric_seconds",
            "既有 langgraph_service.metrics 直方图桥接（秒）",
            ("name",),
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
        )
    return _inproc_hist


def _bridge_in_process_metrics() -> None:
    """把 `langgraph_service.metrics.snapshot()` 的直方图并入 `kb_inproc_metric_seconds`。

    metrics.record(name, ms) 覆盖大量节点/阶段耗时；合并后 Prometheus 才能看到
    既有埋点的聚合，避免「只有新的埋点可见、旧的全不可见」。
    """
    hist = _ensure_inproc_hist()
    if hist is None:
        return
    try:
        from langgraph_service import metrics as lm

        snap = lm.snapshot()
    except Exception:  # noqa: BLE001 — 只跑网关的进程无该模块，跳过桥接
        return
    for name, values in (snap.get("hist") or {}).items():
        for v in values:
            hist.labels(name=name).observe(float(v) / 1000.0)


#: 全局单例：业务 / 中间件 / 节点统一从这里打点
metrics = Metrics()


def p99(name: str = "retrieval") -> float:
    return metrics.p99_seconds(name)


__all__ = [
    "Metrics",
    "metrics",
    "p99",
    "CONTENT_TYPE_LATEST",
]