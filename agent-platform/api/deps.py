"""依赖注入：存储容器、DB 会话、当前用户、知识库权限。

**权限强制的唯一准入点**（裁决 #4）：
`kb_id` 分区/过滤只是检索优化，绝不作为安全边界。任何触及知识库数据的读写，
都必须先过 `require_kb_access`——它在查询路径上以 `owner_id` 强制校验归属。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AuthenticationError, NotFound
from core.logging import get_logger
from core.storage import StorageContainer
from db.models import KnowledgeBase, User

logger = get_logger(__name__)


def get_container(request: Request) -> StorageContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover — 说明 lifespan 没跑起来
        raise RuntimeError("StorageContainer 未初始化：应用 lifespan 未执行")
    return container


async def get_session(
    container: Annotated[StorageContainer, Depends(get_container)],
) -> AsyncIterator[AsyncSession]:
    async with container.mysql.session() as session:
        yield session


def get_user_id(request: Request) -> int:
    """从 scope.state 取 AuthMiddleware 写入的 user_id。"""
    user_id = (request.scope.get("state") or {}).get("user_id")
    if user_id is None:
        raise AuthenticationError("未认证")
    return int(user_id)


async def current_user(
    user_id: Annotated[int, Depends(get_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    user = await session.get(User, user_id)
    if user is None or user.status != 1:
        raise AuthenticationError("用户不存在或已停用")
    return user


async def require_kb_access(
    kb_id: int,
    user: User,
    session: AsyncSession,
) -> KnowledgeBase:
    """查询路径强制权限校验：知识库必须属于当前用户。

    不存在与无权访问返回同一语义（404），避免通过状态码探测他人 kb_id 是否存在。
    """
    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    kb = (await session.execute(stmt)).scalar_one_or_none()

    if kb is None or kb.status != 1 or kb.owner_id != user.id:
        logger.info(
            "kb access denied",
            extra={"extra_fields": {"kb_id": kb_id, "user_id": user.id}},
        )
        raise NotFound("知识库不存在")

    return kb


CurrentUser = Annotated[User, Depends(current_user)]
DBSession = Annotated[AsyncSession, Depends(get_session)]
Container = Annotated[StorageContainer, Depends(get_container)]


__all__ = [
    "Container",
    "CurrentUser",
    "DBSession",
    "current_user",
    "get_container",
    "get_session",
    "get_user_id",
    "require_kb_access",
]
