"""SQLAlchemy 2.x 声明式基类与公共列。

表结构对应《TechnicalDesign》§5.2 的 DDL，二者必须保持一致：
- `db/sql/01_schema.sql` 是开发栈初始化用的权威 DDL；
- 本模块是 ORM 侧等价表达，供业务代码读写。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """全部业务表的声明式基类。"""

    def to_dict(self) -> dict[str, object]:
        """浅序列化，便于日志与调试。"""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class TimestampMixin:
    """毫秒精度时间戳。MySQL 侧用 DATETIME(3)，与 DDL 一致。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(3), server_default=text("CURRENT_TIMESTAMP(3)"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(3),
        server_default=text("CURRENT_TIMESTAMP(3)"),
        server_onupdate=text("CURRENT_TIMESTAMP(3)"),
        nullable=False,
    )
