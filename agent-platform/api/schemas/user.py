"""用户域请求/响应模型（增量 2 · 2.1）。

约定：`api_key` 明文**只在创建与轮换时返回一次**（`api_key` 字段），
其余任何查询路径都不回传 api_key，避免明文二次暴露。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)


class UserPublic(BaseModel):
    """用户公开信息：不含 api_key。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: int
    created_at: datetime | None = None


class UserWithKey(UserPublic):
    """创建/轮换后的临时视图：api_key 明文在此出现一次。"""

    api_key: str


class ApiKeySecret(BaseModel):
    api_key: str = Field(description="新的 api_key 明文，仅此一次展示")


__all__ = ["ApiKeySecret", "UserCreate", "UserPublic", "UserUpdate", "UserWithKey"]