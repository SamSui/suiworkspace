"""SSE 事件编码（对齐已冻结契约，增量 3.5）。

冻结契约（见 `api/upstream.py` 头注释 / `docs/increment-1-breakdown.md` §2.3）：
- `event: token`      data: {"seq":N,"text":"..."}
- `event: interrupt`  data: {"seq":N,"reason":"human_approval","payload":{...}}
- `event: done`       data: {"seq":N,"message_id":123,"usage":{"prompt":..,"completion":..}}
- `event: error`      data: {"seq":N,"code":"llm_timeout","message":"...","trace_id":"..."}
- 心跳 `: ping`（保活，应对 LB 空闲掐断）

`seq` 单调递增跨事件；`trace_id` 由调用方注入 error 事件并使链路贯穿。
"""

from __future__ import annotations

import json
import time
from typing import Any

# 心跳间隔：低于常代 LB 空闲掐断阈值
HEARTBEAT_INTERVAL = 10.0


class SSEEncoder:
    """逐步产出 SSE 数据块。每次 `encode_event` 返回一个完整事件字符串。"""

    def __init__(self) -> None:
        self._seq = 0
        self._last_beat = 0.0

    def encode_event(self, event: str, data: dict[str, Any]) -> str:
        data_payload = dict(data)
        data_payload.setdefault("seq", self._next_seq())
        body = json.dumps(data_payload, ensure_ascii=False)
        return f"event: {event}\ndata: {body}\n\n"

    def heartbeat(self) -> str:
        return ": ping\n\n"

    def maybe_heartbeat(self, now: float | None = None) -> str:
        """距上次心跳超过阈值则发一帧心跳，否则返回空串（流式循环里调用）。"""
        now = now or time.monotonic()
        if now - self._last_beat >= HEARTBEAT_INTERVAL:
            self._last_beat = now
            return self.heartbeat()
        return ""

    def token(self, text: str) -> str:
        return self.encode_event("token", {"text": text})

    def interrupt(self, *, reason: str = "human_approval", payload: dict[str, Any] | None = None) -> str:  # noqa: E501 契约字段名长
        return self.encode_event("interrupt", {"reason": reason, "payload": payload or {}})

    def done(self, *, message_id: int, usage: dict[str, int]) -> str:
        return self.encode_event("done", {"message_id": message_id, "usage": usage})

    def error(self, *, code: str, message: str, trace_id: str) -> str:
        return self.encode_event("error", {"code": code, "message": message, "trace_id": trace_id})

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq


def encode_done(message_id: int, usage: dict[str, int]) -> str:
    """一次性 `done` 事件（非流式收敛路径 / 单测便捷）。"""
    enc = SSEEncoder()
    return enc.done(message_id=message_id, usage=usage)


__all__ = ["SSEEncoder", "HEARTBEAT_INTERVAL", "encode_done"]