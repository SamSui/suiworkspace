"""知识库路由（增量 2.2：知识库 CRUD + 权限）。

`/v1/kb` 全套 CRUD。所有**读取/变更**路径必须过 `require_kb_access`（裁决 #4）：
权限校验在查询路径以 `owner_id` 强制，越权与不存在统一 404（不泄漏存在性）。

| 方法 | 路径 | 权限边界 |
|---|---|---|
| GET    | `/v1/kb`       | 列出**当前用户**的库（owner_id 过滤），无他人资源样本 |
| POST   | `/v1/kb`       | 创建自己名下新库（owner=当前用户） |
| GET    | `/v1/kb/{id}`  | `kb_access`：越权/不存在 → 404 |
| PATCH  | `/v1/kb/{id}`  | `kb_access`：越权/不存在 → 404 |
| DELETE | `/v1/kb/{id}`  | `kb_access`：越权/不存在 → 404；软删（status=0） |

删除采用**软删**（status=0）：与 `require_kb_access` 的 `status != 1 → 404` 语义一致，
被删库即刻对所有路径不可见，且保留行级数据供审计/恢复（本次不级联清理相关表）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from api.deps import CurrentUser, DBSession, kb_access
from api.schemas.kb import (
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    kb_to_out,
)
from db.models import KnowledgeBase

router = APIRouter(prefix="/v1/kb", tags=["knowledge"])


@router.get("", response_model=list[KnowledgeBaseOut])
async def list_knowledge_bases(
    user: CurrentUser,
    session: DBSession,
) -> list[KnowledgeBaseOut]:
    """列出当前用户的知识库（按 owner_id + status 过滤，隔离他人/已删库）。"""
    rows = await session.execute(
        select(KnowledgeBase)
        .where(KnowledgeBase.owner_id == user.id, KnowledgeBase.status == 1)
        .order_by(KnowledgeBase.id)
    )
    return [kb_to_out(kb) for kb in rows.scalars().all()]


@router.post("", response_model=KnowledgeBaseOut, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    user: CurrentUser,
    session: DBSession,
) -> KnowledgeBaseOut:
    """创建知识库，owner 恒为当前用户。"""
    kb = KnowledgeBase(name=payload.name, owner_id=int(user.id), status=1)
    session.add(kb)
    await session.flush()
    await session.refresh(kb)  # 取 server_default 的 created_at（异步下禁属性懒加载）
    return kb_to_out(kb)


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
async def get_knowledge_base(kb: KnowledgeBase = Depends(kb_access)) -> KnowledgeBaseOut:
    return kb_to_out(kb)


@router.patch("/{kb_id}", response_model=KnowledgeBaseOut)
async def update_knowledge_base(
    payload: KnowledgeBaseUpdate,
    kb: KnowledgeBase = Depends(kb_access),
) -> KnowledgeBaseOut:
    kb.name = payload.name
    return kb_to_out(kb)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(kb: KnowledgeBase = Depends(kb_access)) -> None:
    kb.status = 0
    return None