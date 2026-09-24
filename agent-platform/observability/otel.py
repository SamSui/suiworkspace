"""OpenTelemetry 埋点：`trace_id` 贯穿全链路（增量 5.3）。

设计要旨（对齐已冻结的 `core/logging.py` trace_id 贯穿，不重开契约）：
- 仓库已有 `x-request-id`（`TRACE_ID_HEADER`）在一请求内贯穿「网关 → 编排 →
  存储/LLM」的日志；本模块把它**桥接**进观测：用该字符串作为 trace_id，把同一次
  请求的所有 span 挂在**同一 trace_id** 下，使链路图完整、/metrics 可展示。
- `span(name, **attrs)` 是 async 与 sync 均可用的上下文管理器：
  进入时快照 start 与当前 trace_id，退出时把 SpanRecord 写进进程内
  `InMemorySpanSink`（无外部 OTLP collector 也能本地精确验证）。
- 依赖策略：`observability` extra 未安装或 SDK 缺失时，模块仍有完整的 no-op /
  in-memory 行为，调用方不因缺依赖而报错——满足「未验证第三方契约不引入」约束。

与既有 `langgraph_service/metrics.py` 分工：
- `metrics`：进程内轻量计数/直方图（增量 3 已有，压测 P99 断言用）；
- 本模块：跨度结构的 Trace（谁 → 谁、起止、耗时）+ Prometheus 直方图桥接（见 prom.py）。
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import threading
import time
from typing import Any

from core.logging import TRACE_ID_HEADER, get_trace_id

_MONO = time.monotonic
_OTLP_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"


@dataclasses.dataclass
class SpanRecord:
    """一次 span 的结构化记录——不依赖 exporter，测试与报告共用。"""

    name: str
    trace_id: str
    start_ms: float
    duration_ms: float
    parent_name: str | None = None
    attributes: dict[str, str] = dataclasses.field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "trace_id": self.trace_id,
            "duration_ms": round(self.duration_ms, 3),
        }
        if self.parent_name:
            payload["parent"] = self.parent_name
        if self.attributes:
            payload["attributes"] = self.attributes
        return payload


class InMemorySpanSink:
    """线程安全的内存 span 汇集器（GIL + lock）。无外部 collector 的本地验证主路径。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spans: list[SpanRecord] = []

    def record(self, span: SpanRecord) -> None:
        with self._lock:
            self._spans.append(span)

    def take(self, *, name: str | None = None) -> list[SpanRecord]:
        """取走匹配 span 并清空。name=None 取全部。可多次调用。"""
        with self._lock:
            if name is None:
                out, self._spans = self._spans, []
                return out
            matched = [s for s in self._spans if s.name == name]
            self._spans = [s for s in self._spans if s.name != name]
            return matched

    def spans(self) -> list[SpanRecord]:
        with self._lock:
            return list(self._spans)


_sink: InMemorySpanSink | None = None
_sink_lock = threading.Lock()


def get_sink() -> InMemorySpanSink:
    global _sink
    if _sink is None:
        with _sink_lock:
            if _sink is None:
                _sink = InMemorySpanSink()
    return _sink


def reset_observability() -> None:
    """测试用：清空内存 span 记录。"""
    get_sink().take()


class SpanContext:
    """一个 span 的运行时上下文。`with span("retrieve") as s:` 使用。

    进入时快照 start 与当前 trace_id；不论包体是否抛异常，退出都写入 SpanRecord。
    可选地通过 `child()` 挂子 span（形成 Chain，供报告 layout）。支持作为
    decorator 与 async 上下文使用（见模块级 `span` / `async_span` / `spanned`）。
    """

    def __init__(self, name: str, **attributes: Any) -> None:
        self.name = name
        self.attributes = dict(attributes)
        self.start = _MONO()
        self.duration_ms = 0.0
        self.trace_id = get_trace_id() or ""
        self.parent: SpanContext | None = None
        self.children: list[SpanContext] = []

    # -- 分层 --
    def child(self, name: str, **attributes: Any) -> SpanContext:
        sub = SpanContext(name, **attributes)
        sub.parent = self
        self.children.append(sub)
        return sub

    # -- 生命周期 --
    def __enter__(self) -> SpanContext:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.duration_ms = (_MONO() - self.start) * 1000.0
        get_sink().record(
            SpanRecord(
                name=self.name,
                trace_id=self.trace_id,
                start_ms=self.start,
                duration_ms=self.duration_ms,
                parent_name=self.parent.name if self.parent else None,
                attributes={k: str(v) for k, v in self.attributes.items()},
            )
        )
        return False


def span(name: str, **attributes: Any) -> SpanContext:
    """命名 span（同步/异步通用）。"""
    return SpanContext(name, **attributes)


@contextlib.asynccontextmanager
async def async_span(name: str, **attributes: Any):
    """async 版本：`async with async_span("generate") as s:`。"""
    ctx = SpanContext(name, **attributes)
    with ctx:
        yield ctx


def spanned(func):
    """装饰器：把函数/协程整体包成同名 span（span 名 = `qualname`）。"""

    if hasattr(func, "__qualname__") is False:
        return func

    import inspect

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def _async_wrap(*args, **kwargs):
            ctx = SpanContext(func.__qualname__)
            with ctx:
                return await func(*args, **kwargs)

        return _async_wrap

    @functools.wraps(func)
    def _sync_wrap(*args, **kwargs):
        ctx = SpanContext(func.__qualname__)
        with ctx:
            return func(*args, **kwargs)

    return _sync_wrap


def trace_id() -> str:
    """当前请求 trace_id（与日志一致；无请求上下文则空串）。"""
    return get_trace_id()


# ---------------------------------------------------------------------------
# 链路布局：把一次请求的完整 span 树还原为便于展示的文本（验收 5.3“链路图可还原”）
# ---------------------------------------------------------------------------


def format_chain(*spans: SpanContext) -> str:
    """把一个根 span（含其 children 树）排版成缩进链路。传入多个则并列。"""
    lines: list[str] = []

    def _walk(s: SpanContext, depth: int) -> None:
        lines.append(f"{'  ' * depth}{s.name}: {s.duration_ms:.1f}ms")
        for c in s.children:
            _walk(c, depth + 1)

    for root in spans:
        _walk(root, 0)
    return "\n".join(lines)


__all__ = [
    "span",
    "async_span",
    "spanned",
    "SpanContext",
    "SpanRecord",
    "InMemorySpanSink",
    "get_sink",
    "reset_observability",
    "trace_id",
    "format_chain",
    "TRACE_ID_HEADER",
]