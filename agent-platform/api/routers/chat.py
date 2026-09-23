"""对话路由（增量 2 交付）。

对外契约草案：网关不直接访问 Milvus/ES/LLM，只把请求翻译成一次
`langgraph_service` 的内部 HTTP+SSE 调用，并把流透传给前端（裁决 #1）。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.exceptions import NotImplementedYet

router = APIRouter(prefix="/v1", tags=["chat"])

_INCREMENT = "增量 2（api 网关）"


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    kb_id: int | None = Field(default=None, description="指定知识库则走 RAG，否则直答")
    conversation_id: int | None = None


@router.post("/chat")
async def chat(req: ChatRequest) -> None:
    """非流式对话。"""
    raise NotImplementedYet(_INCREMENT)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> None:
    """SSE 流式对话——网关侧只做透传，不缓冲。"""
    raise NotImplementedYet(_INCREMENT)


@router.post("/chat/resume")
async def chat_resume(thread_id: str, value: str) -> None:
    """Human-in-the-loop 恢复：从 RedisSaver 的 checkpoint 续跑。"""
    raise NotImplementedYet(_INCREMENT)
