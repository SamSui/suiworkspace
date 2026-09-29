"""RBAC 统一准入（SUIG-34 · P2 多租户 + RBAC）。

模型：`Tenant → User → Role → Permission`。本模块提供三件套：
  - `ensure_user_permission_seed`：为新用户补齐「一人一默认租户 + admin 角色 + 全权限」，
    与 `db/sql/02_rbac.sql` 第 8 节迁移口径一致（幂等）。
  - `current_permission_codes`：取用户在**其所属租户**内生效的权限 code 集合；
    admin 角色短路返回全量系统权限，避免每租户都挂满。
  - `require_permission`：FastAPI 依赖工厂，按 code 做角色权限准入（拒绝→403）。

租户隔离语义（裁决 #4 延伸）：
  - 越权（跨租户）与不存在统一 **404**，不泄漏他人对象存在性；
  - 无角色 / 无某权限 → **403**（RBAC 准入，验收口径②）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import current_user, get_session
from core.exceptions import PermissionDenied
from db.models import (
    Permission,
    Role,
    RolePermission,
    Tenant,
    User,
    UserRole,
)

# 系统内置权限码（与 db/sql/02_rbac.sql 第 7 节种子一致，二者必须同步）
DEFAULT_PERMISSION_CODES: tuple[str, ...] = (
    "kb:list",
    "kb:read",
    "kb:create",
    "kb:update",
    "kb:delete",
    "agent:list",
    "agent:create",
    "doc:upload",
    "doc:read",
    "doc:delete",
    "task:read",
)

_ADMIN_ROLE_CODE = "admin"


async def ensure_user_permission_seed(session: AsyncSession, user: User) -> None:
    """为单个用户补齐 RBAC 基座（幂等）：
    - user.tenant_id 为空 → 建默认租户（code=t{user_id}）并回填；
    - 该租户下若无 admin 角色则创建；
    - admin 角色挂满 DEFAULT_PERMISSION_CODES；
    - user.role_id 指向 admin，并写 sys_user_role。

    复用迁移语义，保证「既有单用户（P0 前）」与「新注册用户」行为一致（验收③）。
    """
    tenant_id = int(user.tenant_id) if user.tenant_id else 0
    if not tenant_id:
        tcode = f"t{user.id}"
        tup = (
            await session.execute(select(Tenant).where(Tenant.code == tcode).limit(1))
        ).scalar_one_or_none()
        if tup is None:
            tup = Tenant(name=f"默认租户-{user.id}", code=tcode, status=1)
            session.add(tup)
            await session.flush()
        user.tenant_id = int(tup.id)
        tenant_id = int(tup.id)

    role = (
        await session.execute(
            select(Role).where(Role.tenant_id == tenant_id, Role.code == _ADMIN_ROLE_CODE).limit(1)
        )
    ).scalar_one_or_none()
    if role is None:
        role = Role(tenant_id=tenant_id, name="管理员", code=_ADMIN_ROLE_CODE, status=1)
        session.add(role)
        await session.flush()

    existing_ids: set[int] = set(
        (
            await session.execute(
                select(RolePermission.permission_id).where(RolePermission.role_id == int(role.id))
            )
        ).scalars()
    )
    for p in (
        await session.execute(
            select(Permission).where(Permission.code.in_(DEFAULT_PERMISSION_CODES))
        )
    ).scalars().all():
        if int(p.id) not in existing_ids:
            session.add(RolePermission(role_id=int(role.id), permission_id=int(p.id)))

    user.role_id = int(role.id)
    bound = (
        await session.execute(
            select(UserRole.id).where(
                UserRole.user_id == int(user.id), UserRole.role_id == int(role.id)
            ).limit(1)
        )
    ).scalar_one_or_none()
    if bound is None:
        session.add(UserRole(user_id=int(user.id), role_id=int(role.id)))


async def current_permission_codes(session: AsyncSession, user: User) -> set[str]:
    """用户在其默认租户内、经角色聚合出的有效权限 code 集合。"""
    if not user.tenant_id or not user.role_id:
        return set()

    role = (
        await session.execute(
            select(Role).where(Role.id == int(user.role_id), Role.status == 1).limit(1)
        )
    ).scalar_one_or_none()
    if role is None:
        return set()

    # admin 角色拥有全部系统权限
    if role.code == _ADMIN_ROLE_CODE:
        return set(DEFAULT_PERMISSION_CODES)

    rows = (
        await session.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == int(role.id), Permission.status == 1)
        )
    ).all()
    return {c for (c,) in rows}


def require_permission(code: str):
    """FastAPI 依赖工厂：要求当前用户拥有 `code` 权限。

    用法：``_ = Depends(require_permission("kb:read"))``。
    无角色 / 无该权限 → 抛 `PermissionDenied(403)`。
    """

    async def _dep(
        user: Annotated[User, Depends(current_user)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> None:
        codes = await current_permission_codes(session, user)
        if code not in codes:
            raise PermissionDenied(f"缺少权限：{code}")

    return _dep


__all__ = [
    "DEFAULT_PERMISSION_CODES",
    "current_permission_codes",
    "ensure_user_permission_seed",
    "require_permission",
]