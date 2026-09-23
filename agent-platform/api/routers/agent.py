"""Agent 配置路由（增量 2 交付）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.exceptions import NotImplementedYet

router = APIRouter(prefix="/v1/agent", tags=["agent"])

_INCREMENT = "增量 2（api 网关）"


class AgentConfigCreate(BaseModel):
    kb_id: int
    name: str = Field(min_length=1, max_length=128)
    graph_type: str = Field(description="rag_graph / tool_graph / ...")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_k: int = Field(default=5, ge=1, le=50)
    conf: dict[str, Any] = Field(default_factory=dict)


class AgentConfigOut(AgentConfigCreate):
    id: int
    version: int
    status: int


@router.get("", response_model=list[AgentConfigOut])
async def list_agent_configs(_kb_id: int) -> None:
    raise NotImplementedYet(_INCREMENT)


@router.post("", response_model=AgentConfigOut, status_code=201)
async def create_agent_config(_payload: AgentConfigCreate) -> None:
    raise NotImplementedYet(_INCREMENT)
