"""用户路由（增量 2.1：用户与鉴权体系，子任务 user CRUD + api_key 哈希轮换）。

- `POST /v1/users`            公开：创建用户（引导首个用户），api_key 明文回显一次
- `GET  /v1/users/me`         需认证：当前用户资料
- `GET  /v1/users/{id}`       需认证：按 id 取用户
- `POST /v1/users/me/api-key/rotate`  需认证：轮换 api_key（旧 key 即刻失效）

权限粘连（`es_chunk_ref`、`require_kb_access`）属 2.2，本增量不接入（架构放行口径）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from api.deps import CurrentUser, DBSession, get_container
from api.schemas.auth import (
    ApiKeyRotateResult,
    UserCreate,
    UserCreateResult,
    UserOut,
    user_to_out,
)
from core.exceptions import Conflict, NotFound
from core.logging import get_logger
from core.security import generate_api_key, hash_api_key
from db.models import User

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/users", tags=["users"])


@router.post("", response_model=UserCreateResult, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    container=Depends(get_container),
) -> UserCreateResult:
    """创建用户并签发初始 api_key（明文仅此一次返回）。

    公开入口：用于引导首个用户 / 自助注册。名字重复报 409，避免同一 org 撞用户。
    """
    async with container.mysql.session() as session:
        dup = (
            await session.execute(
                select(User.id).where(User.name == payload.name).limit(1)
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise Conflict("用户名已存在") from None

        plain = generate_api_key()
        user = User(name=payload.name, api_key=hash_api_key(plain), status=1)
        session.add(user)
        await session.flush()
        # server_default 的 created_at 在 flush 后于库侧生成，需显式回读（异步下禁止属性懒加载）。
        await session.refresh(user)
        user_id_out = int(user.id)
        created_at = user.created_at

    return UserCreateResult(
        user=UserOut(id=user_id_out, name=payload.name, status=1, created_at=created_at),
        api_key=plain,
    )


@router.get("/me", response_model=UserOut)
async def get_me(user: CurrentUser) -> UserOut:
    return user_to_out(user)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: int, session: DBSession) -> UserOut:
    user = await session.get(User, user_id)
    if user is None or user.status != 1:
        raise NotFound("用户不存在")
    return user_to_out(user)


@router.post("/me/api-key/rotate", response_model=ApiKeyRotateResult)
async def rotate_api_key(user: CurrentUser, session: DBSession) -> ApiKeyRotateResult:
    """轮换当前用户的 api_key：生成新明文、落库新哈希，旧 key 即刻失效。"""
    plain = generate_api_key()
    user.api_key = hash_api_key(plain)
    return ApiKeyRotateResult(user=user_to_out(user), api_key=plain)