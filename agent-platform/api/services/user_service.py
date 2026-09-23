"""用户域业务逻辑（增量 2 · 2.1）。

- `api_key` 单例约束：创建/轮换时由 `core.security` 生成明文并立即哈希入库，
  明文只在返回给调用方的那一刻存在，之后库中仅有哈希。
- 停用 = `status=0`；`current_user`（`api/deps.py`）在状态非 1 时拒绝放行，
  因此停用即生效，无需额外权限层联动。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.exceptions import NotFound
from core.logging import get_logger
from core.security import rotate_api_key
from db.models import User

logger = get_logger(__name__)


class UserService:
    """用户 CRUD + api_key 哈希轮换的薄服务层（直接操作 User ORM）。"""

    async def create(self, session: AsyncSession, name: str) -> tuple[User, str]:
        """创建用户，返回 (user, api_key_明文)。明文仅此一次。"""
        plain, hashed = rotate_api_key(get_settings().app)
        user = User(name=name, api_key=hashed, status=1)
        session.add(user)
        await session.flush()  # 触发 INSERT，拿到自增 id
        # 回读服务端默认值（created_at/updated_at）：服务端生成后在会话内立即取回，
        # 避免后续在序列化（同步线程）读取未加载属性触发 async 懒加载 MissingGreenlet。
        await session.refresh(user)
        logger.info("user created", extra={"extra_fields": {"user_id": user.id}})
        return user, plain

    async def get(self, session: AsyncSession, user_id: int) -> User:
        user = await session.get(User, user_id)
        if user is None:
            raise NotFound("用户不存在")
        return user

    async def update(self, session: AsyncSession, user_id: int, name: str) -> User:
        user = await self.get(session, user_id)
        user.name = name
        await session.flush()
        await session.refresh(user)
        logger.info("user updated", extra={"extra_fields": {"user_id": user_id}})
        return user

    async def deactivate(self, session: AsyncSession, user_id: int) -> User:
        """停用（status=0）。再次停用幂等，不报错。"""
        user = await self.get(session, user_id)
        user.status = 0
        await session.flush()
        await session.refresh(user)
        logger.info("user deactivated", extra={"extra_fields": {"user_id": user_id}})
        return user

    async def rotate_key(self, session: AsyncSession, user_id: int) -> tuple[User, str]:
        """轮换 api_key：覆盖哈希，返回 (User, 新明文)。旧明文立即失效。"""
        user = await self.get(session, user_id)
        plain, hashed = rotate_api_key(get_settings().app)
        user.api_key = hashed
        await session.flush()
        await session.refresh(user)
        logger.info("api_key rotated", extra={"extra_fields": {"user_id": user_id}})
        return user, plain


user_service = UserService()

__all__ = ["UserService", "user_service"]