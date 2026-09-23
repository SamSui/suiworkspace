"""用户管理路由（增量 2 · 2.1）。

- 全部路由非公开（不在 `is_public` 白名单），经 `AuthMiddleware` 强制认证；
- 写操作同时依赖 `CurrentUser`（`api.deps`），停用用户（status=0）一律拒绝；
- 查询路径不回传 api_key，仅创建/轮换返回明文一次（schemas.UserWithKey / ApiKeySecret）。

说明：用户管理需要合法 JWT 才能调用（本无对外的 bootstrapping 通道），
首个用户/首把密钥由运维侧种子脚本或本模块的 service 在受控环境完成——该路径超出 2.1 范围。
"""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import CurrentUser, DBSession
from api.schemas import ApiKeySecret, UserCreate, UserPublic, UserUpdate, UserWithKey
from api.services.user_service import user_service
from db.models import User

router = APIRouter(prefix="/v1/user", tags=["user"])


@router.post("", response_model=UserWithKey, status_code=201)
async def create_user(
    payload: UserCreate,
    session: DBSession,
    _actor: CurrentUser,
) -> UserWithKey:
    user, plain = await user_service.create(session, payload.name)
    return UserWithKey(
        id=user.id,
        name=user.name,
        status=user.status,
        created_at=user.created_at,
        api_key=plain,
    )


@router.get("/{user_id}", response_model=UserPublic)
async def get_user(
    user_id: int,
    session: DBSession,
    _actor: CurrentUser,
) -> User:
    return await user_service.get(session, user_id)


@router.patch("/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    session: DBSession,
    _actor: CurrentUser,
) -> User:
    if payload.name is None:
        return await user_service.get(session, user_id)
    return await user_service.update(session, user_id, payload.name)


@router.post("/{user_id}/deactivate", response_model=UserPublic)
async def deactivate_user(
    user_id: int,
    session: DBSession,
    _actor: CurrentUser,
) -> User:
    return await user_service.deactivate(session, user_id)


@router.post("/{user_id}/api-key/rotate", response_model=ApiKeySecret)
async def rotate_api_key(
    user_id: int,
    session: DBSession,
    _actor: CurrentUser,
) -> ApiKeySecret:
    _user, plain = await user_service.rotate_key(session, user_id)
    return ApiKeySecret(api_key=plain)