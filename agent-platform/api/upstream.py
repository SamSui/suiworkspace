"""编排服务（langgraph_service）的网关注入客户端。

网关只做透传 / 编排调用，不接检索 / LLM / 存储逻辑（裁决 #1）。
本模块是网关侧唯一触及下游的地方：把一次聊天请求翻译成对 `langgraph_service`
的内部 HTTP+SSE 调用，并：

- `chat_stream` —— 按字节透传编排的 SSE 响应体（**不缓冲**），供 `/v1/chat/stream`
  与 `/v1/chat/resume` 直接 yield 给 StreamingResponse；
- `chat_once`    —— 订阅下游 SSE、读到 `done` 事件后收敛返回，供非流式 `/v1/chat` 使用。

对齐已冻结的 SSE 契约：

```
event: token      data: {"seq":N,"text":"..."}
event: interrupt  data: {"seq":N,"reason":"human_approval","payload":{...}}
event: done       data: {"seq":N,"message_id":123,"usage":{"prompt":..,"completion":..}}
event: error      data: {"seq":N,"code":"llm_timeout","message":"...","trace_id":"..."}
```

下游可能按任意边界分块发送，这里**只透传原始字节**，不重组 / 不解析 / 不缓冲——
断线重连续接去重由前端承担（契约第 1 条）。心跳 `: ping` 一并原样透传（契约第 4 条）。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from core.config import get_settings
from core.exceptions import UpstreamError
from core.logging import get_logger

logger = get_logger(__name__)

# 编排服务下游路由（与 langgraph_service/main.py 的占位契约对齐）。
STREAM_PATH = "/v1/stream"
RESUME_PATH = "/v1/resume"

# SSE 行前缀（按 WHATWG Server-Sent Events 规范解析编排流的事件骨架）。
_EVENT_PREFIX = "event:"
_DATA_PREFIX = "data:"

# 收敛（非流式 /v1/chat）时只认这两个终态事件。
_DONE_EVENT = "done"
_ERROR_EVENT = "error"

# 测试注入点：生产走 settings 默认、真实内网编排；测试替换为指向本地 SSE stub 的 client。
ClientFactory = Callable[[], httpx.AsyncClient]
_client_factory: ClientFactory | None = None


def _default_client_factory() -> httpx.AsyncClient:
    """生产默认：指向 settings.app.langgraph_service_url，长流只读、不设读超时。"""
    base_url = get_settings().app.langgraph_service_url
    # read=None：SSE 长流由心跳保活，对单次读不设超时
    timeout = httpx.Timeout(connect=6.0, read=None, write=30.0, pool=6.0)
    return httpx.AsyncClient(base_url=base_url, timeout=timeout, follow_redirects=False)


def get_client() -> httpx.AsyncClient:
    """返回编排服务客户端（优先测试注入的 factory）。"""
    if _client_factory is not None:  # pragma: no cover — 仅供测试替换
        return _client_factory()
    return _default_client_factory()


def set_client_factory(factory: ClientFactory | None) -> None:
    """测试钩子：注入自定义 client factory（如 httpx.ASGITransport 指向本地编排 stub）。

    传入 None 恢复生产默认。
    """
    global _client_factory
    _client_factory = factory


async def _post_stream(
    client: httpx.AsyncClient, path: str, payload: dict[str, Any]
) -> httpx.Response:
    """发起一次下游流式 POST，对响应体不做任何预读。"""
    try:
        return await client.post(path, json=payload)
    except httpx.HTTPError as exc:
        logger.warning(
            "upstream request failed",
            extra={"extra_fields": {"path": path, "error": type(exc).__name__}},
        )
        raise UpstreamError(f"编排服务请求失败: {type(exc).__name__}") from exc


def _ensure_2xx(resp: httpx.Response) -> None:
    """非 2xx → 502，不把下游错误状态伪装成成功流。"""
    if resp.is_error or resp.status_code >= 400:
        raise UpstreamError(f"编排服务返回 {resp.status_code}")


async def chat_stream(path: str, payload: dict[str, Any]) -> AsyncIterator[bytes]:
    """按字节透传下游 SSE 响应体（**不缓冲**），供流式端点直接 yield。

    返回 async 迭代器，按下游分块的原始字节序向外 yield，交由 StreamingResponse
    立即写出；下游尚未发帧前本端不攒响应体。连接 / 状态错误在首个块前抛出；
    已开始透传后下游中断则自然断开（对 SSE 是合理半开语义——前端按 seq 续接）。
    """
    client = get_client()
    try:
        resp = await _post_stream(client, path, payload)
        _ensure_2xx(resp)
    except UpstreamError:
        await client.aclose()
        raise

    async def _forward() -> AsyncIterator[bytes]:
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return _forward()


@dataclasses.dataclass
class ChatOnceResult:
    """非流式 `/v1/chat` 的收敛载荷：从编排 `done` 事件摘出。"""

    thread_id: str
    message_id: int
    usage: dict[str, int]


def _parse_done_data(raw: str) -> tuple[int, dict[str, int]]:
    """把 `done` 事件的 data JSON 解析成 (message_id, usage)。

    冻结契约的 `done` 只带 message_id + usage，thread_id 由网关注入端持有，故不从这里取。
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpstreamError("编排 done 事件格式非法") from exc
    message_id = payload.get("message_id")
    usage = payload.get("usage") or {}
    if message_id is None:
        raise UpstreamError("编排 done 事件缺少 message_id")
    return int(message_id), {str(k): int(v) for k, v in usage.items()}


def _raise_error_from_data(raw: str) -> None:
    """把 `error` 事件的 `data` 转成带 code/trace_id 的 502。"""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    code = str(payload.get("code") or "upstream_error")
    message = str(payload.get("message") or "编排流错误")
    detail: dict[str, Any] = {"code": code, "message": message}
    if payload.get("trace_id"):
        detail["trace_id"] = payload["trace_id"]
    exc = UpstreamError(f"{code}: {message}")
    exc.detail = detail
    raise exc


async def chat_once(thread_id: str, payload: dict[str, Any]) -> ChatOnceResult:
    """订阅下游 SSE，等待 `done` 并收敛返回（供非流式 `/v1/chat`）。

    非流式端点允许缓冲：完整读下游、逐事件解析；遇 `error` → 抛带 code/trace_id 的
    502；读到 `done` → 返回 `thread_id` + `message_id` + `usage`（前端据此对齐 message 行）。
    """
    client = get_client()
    try:
        resp = await _post_stream(client, STREAM_PATH, payload)
        _ensure_2xx(resp)
    except UpstreamError:
        await client.aclose()
        raise

    try:
        event: str | None = None
        async for line in resp.aiter_lines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(_EVENT_PREFIX):
                event = stripped[len(_EVENT_PREFIX) :].strip()
                continue
            if stripped.startswith(_DATA_PREFIX):
                raw = stripped[len(_DATA_PREFIX) :].strip()
                if event == _DONE_EVENT:
                    message_id, usage = _parse_done_data(raw)
                    return ChatOnceResult(
                        thread_id=thread_id, message_id=message_id, usage=usage
                    )
                if event == _ERROR_EVENT:
                    _raise_error_from_data(raw)
                # 其余事件（token/interrupt）收敛时忽略；event 归零等待下一条
                event = None
                continue
            # 心跳 `: `、未知 field 行一律忽略
            continue
        raise UpstreamError("编排流在产生 done 之前结束")
    finally:
        await resp.aclose()
        await client.aclose()