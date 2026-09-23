"""对话路由（增量 2.3：对话接口 SSE）。

网关只做透传 / 编排调用，不接检索 / LLM / 存储逻辑（裁决 #1）：聊天请求被翻译成
一次对 `langgraph_service` 的内部 HTTP+SSE 调用，流透传给前端（裁决 #1）。

| 方法 | 路径              | 行为 |
|------|-------------------|------|
| POST | `/v1/chat`        | 非流式：订阅编排流、读到 `done` 后返回 thread/message id |
| POST | `/v1/chat/stream` | SSE **逐字节透传**编排流，不缓冲 |
| POST | `/v1/chat/resume` | 以 thread_id 续访挂起的图；无该会话/无权 → 404 |

对齐已冻结 SSE 契约：`token` / `interrupt` / `done` / `error` 事件字段、`seq`
单调递增、`error` 的 `code`+`trace_id` 进 body、`done` 带 `message_id`+`usage`、
心跳 `: ping` 保活。这些都由编排产生、网关原样透传，本层不自造事件结构。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from api import upstream
from api.deps import CurrentUser, DBSession, get_user_id, require_thread_access
from api.schemas.chat import (
    ChatRequest,
    ChatStartResponse,
    ChatStreamRequest,
    ResumeRequest,
)

router = APIRouter(prefix="/v1", tags=["chat"])

# SSE 透传响应头：禁止缓存 / 要求代理不缓冲（配合 :ping 心跳应对 LB 空闲掐断）
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _new_thread_id() -> str:
    """新建会话的寻址键（langgraph thread_id）。对齐模型 String(64) 上限。"""
    return uuid.uuid4().hex[:64]


@router.post("/chat", response_model=ChatStartResponse)
async def chat(
    req: ChatRequest,
    user: CurrentUser,
) -> ChatStartResponse:
    """非流式对话：封装一次聊天请求 → 调编排 → 返回 message/thread id。

    网关不透传 token/interrupt（非流式即收敛）：等待编排 `done` 事件，摘出
    `message_id` + `usage` 返回（前端据此与 MySQL message 行对齐）；编排 `error`
    事件则转 502，且 `trace_id` 已落在响应 body（契约第 2 条）。
    """
    thread_id = req.thread_id or _new_thread_id()
    payload = {
        "thread_id": thread_id,
        "query": req.query,
        "user_id": int(user.id),
        "kb_id": req.kb_id,
    }
    result = await upstream.chat_once(thread_id, payload)
    return ChatStartResponse(
        thread_id=result.thread_id,
        conversation_id=req.conversation_id,
        message_id=result.message_id,
        usage=result.usage,
    )


@router.post("/chat/stream")
async def chat_stream(
    req: ChatStreamRequest,
    user_id: Annotated[int, Depends(get_user_id)],
) -> StreamingResponse:
    """SSE 流式对话——逐事件透传给编排，不缓冲（关键）。

    网关对编排的 SSE 响应体**按字节转发**（`chat_stream` 返回的迭代器逐 chunk
    yield 给 `StreamingResponse`），不重组、不攒包，确保逐字流式；心跳、event/data
    结构、seq 都由编排产生并原样到达前端。
    """
    thread_id = req.thread_id or _new_thread_id()
    payload = {
        "thread_id": thread_id,
        "query": req.query,
        "user_id": user_id,
        "kb_id": req.kb_id,
    }
    body = await upstream.chat_stream(upstream.STREAM_PATH, payload)
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers=dict(_SSE_HEADERS),
    )


@router.post("/chat/resume")
async def chat_resume(
    req: ResumeRequest,
    user: CurrentUser,
    session: DBSession,
) -> StreamingResponse:
    """以 thread_id 续接挂起的图（HITL 恢复）。

    归属校验：`require_thread_access` 显式校验——该线程不存在、非当前用户所有或已停用 →
    `NotFound(404)`（与知识库越权同语义，不泄漏他人 thread 是否存在）。
    校验通过后透传编排 `/v1/resume` 的 SSE 流。
    """
    await require_thread_access(req.thread_id, user, session)
    payload = {"thread_id": req.thread_id, "value": req.value, "user_id": int(user.id)}
    body = await upstream.chat_stream(upstream.RESUME_PATH, payload)
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers=dict(_SSE_HEADERS),
    )