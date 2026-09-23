"""对话接口的请求 / 响应模型（Pydantic v2）。

网关只透传：这些模型服务于「网关照进编排」的入参 / 收敛出参，
外来载荷（token / interrupt / done / error 事件字段）以已冻结契约原样透传，
本层不重定义事件结构。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """发起 / 续会话的非流式请求。"""

    query: str = Field(min_length=1, max_length=8000)
    kb_id: int | None = Field(default=None, description="指定知识库则走 RAG，否则直答")
    conversation_id: int | None = Field(
        default=None, description="已有会话 id；为空则新建会话"
    )
    thread_id: str | None = Field(
        default=None,
        max_length=64,
        description="会话寻址键（langgraph thread_id）。为空由网关新发；携带则续等同线程",
    )


class ChatStartResponse(BaseModel):
    """`/v1/chat` 的收敛出参：对齐 done 事件的 message_id + usage。"""

    model_config = ConfigDict(extra="allow")

    thread_id: str
    conversation_id: int | None = None
    message_id: int
    usage: dict[str, int] = Field(default_factory=dict, description="token 用量，落库有源")


class ChatStreamRequest(BaseModel):
    """SSE 流式请求（与 ChatRequest 同构，供对照语义：透传不收敛）。"""

    query: str = Field(min_length=1, max_length=8000)
    thread_id: str | None = Field(
        default=None,
        max_length=64,
        description="会话接管键；缺则网关新规一个 thread_id 传入编排",
    )
    conversation_id: int | None = None
    kb_id: int | None = Field(default=None, description="指定知识库则走 RAG，否则直答")


class ResumeRequest(BaseModel):
    """Human-in-the-loop 恢复：以 thread_id 识别会话，续跑挂起的图。"""

    thread_id: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, description="HITL 审批通过后的人工输入 / 决策值")


__all__ = [
    "ChatRequest",
    "ChatStartResponse",
    "ChatStreamRequest",
    "ResumeRequest",
]