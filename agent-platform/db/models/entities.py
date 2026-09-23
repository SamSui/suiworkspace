"""MySQL 业务表 ORM 模型（6 张）。

铁律（设计文档 §5.3）：MySQL 不存大文本与向量。
- `message.content` 只落摘要 + `es_chunk_ref`，正文在 ES / Redis；
- 向量只在 Milvus，通过 `chunk_id` / `doc_id` 回链。
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class DocumentStatus(enum.IntEnum):
    """文档摄入状态机——双写一致性的 source of truth（裁决 #3）。

    推进路径：PENDING → PROCESSING → DONE / FAILED。
    失败置 FAILED，由 worker 按退避重跑；重跑前按 doc_id 清理 Milvus/ES 残留，保证可重入。
    """

    PENDING = 0
    PROCESSING = 1
    DONE = 2
    FAILED = 3


class User(Base, TimestampMixin):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # 只存哈希，不存明文；轮换时整行替换
    api_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 权限校验的唯一依据（裁决 #4）：查询路径强制 owner_id 过滤
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    owner: Mapped[User] = relationship(back_populates="knowledge_bases")
    documents: Mapped[list[Document]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_kb_owner", "owner_id"),)


class Document(Base, TimestampMixin):
    __tablename__ = "document"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_base.id"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # sha256：同一 kb 内重传去重 + 摄入幂等
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("0"),
        comment="DocumentStatus: 0未处理 1处理中 2完成 3失败",
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_msg: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")

    @property
    def status_enum(self) -> DocumentStatus:
        """以枚举语义读取状态——列本身是 TINYINT，与 DDL 保持一致。"""
        return DocumentStatus(self.status)

    __table_args__ = (
        UniqueConstraint("kb_id", "file_hash", name="uk_kb_hash"),
        Index("idx_doc_kb_status", "kb_id", "status"),
    )


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=False)
    kb_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("knowledge_base.id"), nullable=True
    )
    # LangGraph 会话键：多实例共享状态的寻址依据（RedisSaver 的 thread_id）
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_conv_user", "user_id"), Index("idx_conv_thread", "thread_id"))


class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conv_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversation.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user / assistant / tool
    msg_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'text'"))
    # 命中切片 chunk_id 列表（JSON 数组），正文不入库
    es_chunk_ref: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(3),
        server_default=text("CURRENT_TIMESTAMP(3)"),
        nullable=False,
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (Index("idx_msg_conv", "conv_id"),)


class AgentConfig(Base, TimestampMixin):
    __tablename__ = "agent_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_base.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    graph_type: Mapped[str] = mapped_column(String(64), nullable=False)  # rag_graph / tool_graph
    temperature: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.7"))
    top_k: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    conf: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (Index("idx_agent_kb", "kb_id"),)


__all__ = [
    "AgentConfig",
    "Conversation",
    "Document",
    "DocumentStatus",
    "KnowledgeBase",
    "Message",
    "User",
]
