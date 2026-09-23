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
from db.models import Conversation, KnowledgeBase, User

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


async def kb_access(
    kb_id: int,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBase:
    """FastAPI 依赖形态的 `require_kb_access`：把路径参数 `kb_id` 绑定进来。

    需要读写某个知识库的路由，直接用 `Depends(kb_access)` 即可拿到已通过归属校验的 KB。
    校验失败与不存在统一抛 `NotFound(404)`（不泄漏存在性，裁决 #4）。
    注：`current_user` 与这里的 `get_session` 是同一 callable，FastAPI 按依赖去重，
    两者共享同一会话实例。
    """
    return await require_kb_access(kb_id, user, session)


CurrentUser = Annotated[User, Depends(current_user)]
DBSession = Annotated[AsyncSession, Depends(get_session)]
Container = Annotated[StorageContainer, Depends(get_container)]


async def require_thread_access(
    thread_id: str, user: User, session: AsyncSession
) -> Conversation:
    """以 thread_id 取「当前用户拥有」的会话，供续接类路由做归属校验。

    只做归属校验（不接检索 / 生成逻辑）：该线程不存在、非当前用户所有或已停用，
    统一抛 `NotFound(404)`——与知识库越权同语义，不泄漏他人 thread 是否存在。
    由路由显式调用（普通函数，不带 FastAPI 依赖，避免与原 body 的 thread_id 冲突）。

    Returns:
        Conversation：已确认归属的会话行。
    """
    stmt = select(Conversation).where(Conversation.thread_id == thread_id)
    conv = (await session.execute(stmt)).scalar_one_or_none()

    if conv is None or conv.status != "active" or conv.user_id != int(user.id):
        logger.info(
            "thread access denied",
            extra={"extra_fields": {"thread_id": thread_id, "user_id": user.id}},
        )
        raise NotFound("会话不存在")
    return conv


__all__ = [
    "Container",
    "CurrentUser",
    "DBSession",
    "current_user",
    "get_container",
    "get_session",
    "get_user_id",
    "kb_access",
    "require_kb_access",
    "require_thread_access",
]
