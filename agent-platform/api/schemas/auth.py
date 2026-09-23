"""用户与鉴权接口的请求/响应模型（Pydantic v2）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from db.models import User


class UserOut(BaseModel):
    """对外暴露的用户视图——**从不含 api_key（明文或哈希）**。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: int
    created_at: Any


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="用户名，唯一")


class UserCreateResult(BaseModel):
    """创建用户响应：api_key 明文仅此一次返回，落库的一律是哈希。"""

    user: UserOut
    api_key: str = Field(description="明文 api_key，仅此一次返回，请立即安全保存")


class ApiKeyRotateResult(BaseModel):
    """轮换响应：旧 key 即刻失效，新 key 明文仅此一次返回。"""

    user: UserOut
    api_key: str


class TokenRequest(BaseModel):
    """登录请求：用 name + api_key 换取短期 JWT。"""

    name: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=1, description="明文 api_key")


class TokenResponse(BaseModel):
    access_token: str = Field(description="JWT，后续以 Authorization: Bearer <token> 携带")
    token_type: str = "bearer"
    expires_in: int = Field(description="秒")
    user: UserOut


def user_to_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        name=u.name,
        status=u.status,
        created_at=u.created_at,
    )


__all__ = [
    "ApiKeyRotateResult",
    "TokenRequest",
    "TokenResponse",
    "UserCreate",
    "UserCreateResult",
    "UserOut",
    "user_to_out",
]