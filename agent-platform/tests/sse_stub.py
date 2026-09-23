"""编排服务（langgraph_service）的最小 SSE stub —— 供增量 2.3 网关透传的集成测试。

意图：用**符合已冻结契约**的下游 SSE 流，验证网关「逐字节透传 + 校验收敛」的真实链路，
而不依赖增量 3 的真实编排节点。

只实现网关会用到的两个路由：
- `POST /v1/stream`：按请求生成 `token` / `done`（或 `error`）事件流；
- `POST /v1/resume` ：对已知线程输出 `done` 事件（校验 HITL 续访）。

真实的检索 / 生成节点是增量 3 的职责，这里不做——本 stub **只发已冻结事件**，
事件格式严格遵循：

```
event: token      data: {"seq":1,"text":"..."}
event: done       data: {"seq":N,"message_id":..,"usage":{...}}
event: error      data: {"seq":N,"code":"..","message":"..","trace_id":".."}
```

每事件独立作为一次 ASGI body chunk 发出（事件之间 `sleep`），以此校验网关**不缓冲、
逐事件透传**。行为由请求体 `_test` 字段切分好/坏场景。
"""

from __future__ import annotations

import asyncio
import json

_STREAM_DELAY_SECONDS = 0.001


def _token_event(seq: int, text: str) -> str:
    data = {"seq": seq, "text": text}
    return f"event: token\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _done_event(message_id: int, prompt: int, completion: int) -> str:
    data = {
        "seq": 3,
        "message_id": message_id,
        "usage": {"prompt": prompt, "completion": completion},
    }
    return f"event: done\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _error_event(code: str | None = None) -> str:
    data = {
        "seq": 1,
        "code": code or "llm_timeout",
        "message": "上游超时",
        "trace_id": "trace-stub-1",
    }
    return f"event: error\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_all(text: str, message_id: int = 321) -> str:
    """完整 SSE：token(seq1) → 心跳 ping → token(seq2) → done。"""
    return (
        _token_event(1, f"{text}")
        + ": ping\n\n"
        + _token_event(2, "，世界")
        + _done_event(message_id, 11, 7)
    )


class SSEStubApp:
    """最小 ASGI 编排 stub：以冻结契约输出事件，服务网关集成测试。"""

    def __init__(self) -> None:
        self.hits: list[dict] = []  # 记录收到的请求体，供断言透传参数

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            return
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        self.hits.append(payload)

        path = scope["path"]
        method = scope["method"]
        await self._dispatch(send, method, path, payload)

    async def _dispatch(self, send, method: str, path: str, payload):  # noqa: ANN001
        status = 200
        headers = [(b"content-type", b"text/event-stream")]

        if path == "/v1/stream" and method == "POST":
            text = payload.get("query", "hi")
            message_id = int(payload.get("_test", {}).get("message_id", 321))
            if text.strip() == "__error__":
                chunk = _error_event().encode("utf-8")
            else:
                chunk = stream_all(text, message_id=message_id).encode("utf-8")
        elif path == "/v1/resume" and method == "POST":
            chunk = stream_all(payload.get("value", "ok")).encode("utf-8")
        else:
            status = 404
            chunk = b'{"error":"not found"}'

        await send({"type": "http.response.start", "status": status, "headers": headers})
        # 每事件一个独立 body chunk（事件之间 sleep），校验网关不攒包、逐事件透传
        for part in _split_events(chunk):
            await asyncio.sleep(_STREAM_DELAY_SECONDS)
            await send({"type": "http.response.body", "body": part, "more_body": True})
        await send({"type": "http.response.body", "body": b""})


def _split_events(chunk: bytes) -> list[bytes]:
    """把整段 SSE 文本切成「逐事件」的独立 chunk，模拟真实分包的到达顺序。"""
    # 契约事件以空行分隔；把每个完整事件作为一个 chunk，校验网关「逐事件透传」。
    parts = chunk.split(b"\n\n")
    return [p + b"\n\n" for p in parts if p.strip()] or [chunk]


app = SSEStubApp()