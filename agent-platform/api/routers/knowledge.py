"""知识库路由（增量 2 交付）。

所有读取路径必须先过 `require_kb_access`——权限校验在查询路径强制（裁决 #4）。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.exceptions import NotImplementedYet

router = APIRouter(prefix="/v1/kb", tags=["knowledge"])

_INCREMENT = "增量 2（api 网关）"


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class KnowledgeBaseOut(BaseModel):
    id: int
    name: str
    owner_id: int
    status: int


@router.get("", response_model=list[KnowledgeBaseOut])
async def list_knowledge_bases() -> None:
    """列出当前用户的知识库（按 owner_id 过滤）。"""
    raise NotImplementedYet(_INCREMENT)


@router.post("", response_model=KnowledgeBaseOut, status_code=201)
async def create_knowledge_base(payload: KnowledgeBaseCreate) -> None:
    raise NotImplementedYet(_INCREMENT)


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
async def get_knowledge_base(kb_id: int) -> None:
    raise NotImplementedYet(_INCREMENT)
