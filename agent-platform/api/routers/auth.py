"""认证路由（增量 2.1：JWT 签发）。

- `POST /v1/auth/token`  公开：用 name + api_key 换取短期 JWT（Bearer 凭证）
- `POST /v1/auth/logout` 可选：JWT 无状态，返回 204 占位（无服务端吊销语义）

校验只对 api_key 做哈希比对（不存明文）；JWT 只放 `sub`(user_id)，
授权（权限粘连）在 2.2 由 `require_kb_access` 承接，本增量不做。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_container
from api.schemas.auth import TokenRequest, TokenResponse, UserOut
from core.config import get_settings
from core.exceptions import AuthenticationError
from core.logging import get_logger
from core.security import create_access_token, verify_api_key
from db.models import User

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
async def create_token(
    payload: TokenRequest,
    container=Depends(get_container),
) -> TokenResponse:
    """用 `name` + 明文 `api_key` 换取短期 JWT。

    任一不匹配即 401（与 token 无效同语义，避免探测用户是否存在）。
    """
    session: AsyncSession
    async with container.mysql.session() as session:
        row = (
            await session.execute(
                select(
                    User.id, User.name, User.status, User.api_key, User.created_at
                )
                .where(User.name == payload.name)
                .limit(1)
            )
        ).first()

    # 取标量后 session 已关闭；在块内完成哈希比对，避免 DetachedInstance 懒加载。
    if row is None or row.status != 1 or not verify_api_key(payload.api_key, row.api_key):
        logger.info(
            "token reject",
            extra={"extra_fields": {"name": payload.name, "reason": "bad_credential"}},
        )
        raise AuthenticationError("用户名或 api_key 不正确")

    settings = get_settings()
    access_token = create_access_token(settings.app, int(row.id))
    return TokenResponse(
        access_token=access_token,
        expires_in=settings.app.jwt_expire_minutes * 60,
        user=UserOut(
            id=int(row.id),
            name=row.name,
            status=row.status,
            created_at=row.created_at,
        ),
    )