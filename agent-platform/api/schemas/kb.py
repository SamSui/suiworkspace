"""知识库接口的请求/响应模型（Pydantic v2）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from db.models import KnowledgeBase


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class KnowledgeBaseUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    owner_id: int
    status: int


def kb_to_out(kb: KnowledgeBase) -> KnowledgeBaseOut:
    return KnowledgeBaseOut(
        id=int(kb.id),
        name=kb.name,
        owner_id=int(kb.owner_id),
        status=kb.status,
    )


__all__ = [
    "KnowledgeBaseCreate",
    "KnowledgeBaseOut",
    "KnowledgeBaseUpdate",
    "kb_to_out",
]