"""结构化日志 + trace_id 贯穿。

设计要点（对应 TechnicalDesign §9.2）：
- 全链路一个 trace_id：入口中间件生成 → contextvar 携带 → 日志自动注入。
- JSON 结构化输出，可直接被 Loki / ELK 采集。
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar

# 请求级 trace_id；未进入请求上下文时为空串
_trace_id: ContextVar[str] = ContextVar("trace_id", default="")

TRACE_ID_HEADER = "x-request-id"


def set_trace_id(value: str) -> None:
    _trace_id.set(value)


def get_trace_id() -> str:
    return _trace_id.get()


class _TraceIdFilter(logging.Filter):
    """把当前 trace_id 注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "trace_id", ""):
            record.trace_id = get_trace_id() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # 业务自定义字段（logger.info("...", extra={"extra_fields": {...}})）
        if extra := getattr(record, "extra_fields", None):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        return f"{base} trace_id={getattr(record, 'trace_id', '-')}"


def setup_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """幂等地装配 root logger。进程入口各调一次。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_TraceIdFilter())
    if json_output:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            _TextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # 第三方库降噪
    for noisy in ("uvicorn.access", "elastic_transport.transport", "pymilvus"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
