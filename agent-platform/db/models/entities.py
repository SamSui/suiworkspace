"""MySQL 业务表 ORM 模型（V1.0 21 表）。

铁律（设计文档 §5.3）：MySQL 不存大文本与向量。
- `message.content` 只落摘要 + `es_chunk_ref`，正文在 ES / Redis；
- 向量只在 Milvus，通过 `chunk_id` / `doc_id` 回链；
- 大文本（chunk 原文）在 ES，`document_chunk` 只存切片元数据回链。

表集 = 既有 6 表 + V1.0 收敛新增（SUIG-31）：
- 既有保留：`user` / `knowledge_base` / `document` / `conversation` / `message`；
- 演进改名：`agent_config` → `agent`（数据由迁移脚本原样保留，`(改)` 标记）；
- 新增多租户 / 平台：`sys_tenant` / `sys_user` / `sys_role` / `sys_permission`
  / `agent_version` / `agent_tool` / `agent_knowledge` / `document_chunk`
  / `model` / `model_provider` / `tool` / `tool_permission`
  / `agent_run` / `agent_event` / `audit_log`。

本轮仅 DDL 入库、未接入路由的表（在类注释中标 `@DDL-ONLY`）：
`sys_tenant` / `sys_user` / `sys_role` / `sys_permission` / `agent_version`
/ `agent_tool` / `agent_knowledge` / `document_chunk` / `model`
/ `model_provider` / `tool` / `tool_permission` / `agent_run`
/ `agent_event` / `audit_log`。
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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


class AgentStatus(enum.IntEnum):
    """agent 生命周期状态。"""

    DISABLED = 0
    ENABLED = 1
    DRAFT = 2


class RunStatus(enum.IntEnum):
    """agent_run / task 的运行状态机。"""

    RUNNING = 0
    SUCCEEDED = 1
    FAILED = 2
    CANCELLED = 3


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


class SysTenant(Base, TimestampMixin):
    """多租户表（@DDL-only，本轮仅入库不接路由）。"""

    __tablename__ = "sys_tenant"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class SysUser(Base, TimestampMixin):
    """租户内用户（@DDL-only，本轮仅入库不接路由）。

    与既有 `user`（平台用户）并存：`user` 兼容历史链路，`sys_user` 承接多租户收敛。
    本轮仅 DDL 入库，尚未接入权限路由。
    """

    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_tenant.id"), nullable=False
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # 只存哈希，不存明文
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(3), nullable=True)

    tenant: Mapped[SysTenant] = relationship()

    __table_args__ = (Index("idx_sys_user_tenant", "tenant_id"),)


class SysRole(Base, TimestampMixin):
    """租户内角色（@DDL-only）。Tenant → User → Role → Permission 链的中间层。"""

    __tablename__ = "sys_role"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_tenant.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (Index("idx_sys_role_tenant", "tenant_id"),)


class SysPermission(Base, TimestampMixin):
    """租户内权限点（@ORM-only）。"""

    __tablename__ = "sys_permission"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_tenant.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    # resource:action，如 kb:read, agent:write
    resource: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (Index("idx_sys_perm_tenant", "tenant_id"),)


class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 权限校验的唯一依据（裁决 #4）：查询路径强制 owner_id 过滤
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    # 多租户占位（V1.0 预埋，默认 0 表示单租户/平台）
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    owner: Mapped[User] = relationship(back_populates="knowledge_bases")
    documents: Mapped[list[Document]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_kb_owner", "owner_id"),
        Index("idx_kb_tenant", "tenant_id"),
    )


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
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")

    @property
    def status_enum(self) -> DocumentStatus:
        """以枚举语义读取状态——列本身是 TINYINT，与 DDL 保持一致。"""
        return DocumentStatus(self.status)

    __table_args__ = (
        UniqueConstraint("kb_id", "file_hash", name="uk_kb_hash"),
        Index("idx_doc_kb_status", "kb_id", "status"),
        Index("idx_doc_tenant", "tenant_id"),
    )


class DocumentChunk(Base, TimestampMixin):
    """文档切片元数据（@DDL-only）。

    对齐 `ingest/chunker.py` 的 Chunk 结构（index / text / token_count）与
    Milvus / ES 的回链键：`chunk_id` 全局唯一（= ES `_id` = Milvus 主键）。
    铁律：MySQL 不存原文，`text` 仅落 ES；本表只存 count 与回链，不落 large text。
    """

    __tablename__ = "document_chunk"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document.id"), nullable=False
    )
    # 全局回链键，与 ES _id / Milvus 主键一致
    chunk_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    document: Mapped[Document] = relationship()

    __table_args__ = (
        Index("idx_chunk_doc", "doc_id"),
        Index("idx_chunk_tenant", "tenant_id"),
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
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_conv_user", "user_id"),
        Index("idx_conv_thread", "thread_id"),
        Index("idx_conv_tenant", "tenant_id"),
    )


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


class Agent(Base, TimestampMixin):
    """Agent 定义——由既有 `agent_config` 收敛改名而来（`(改)`，数据原样保留）。

    保留原 `agent_config` 全部列语义（kb_id/name/graph_type/temperature/top_k/conf/status/version）
    新增 `tenant_id`、`description` 与类型枚举，扩展为 V1.0 的 agent 主表。
    """

    __tablename__ = "agent"

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
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'rag'"))

    versions: Mapped[list[AgentVersion]] = relationship(back_populates="agent")
    runs: Mapped[list[AgentRun]] = relationship(back_populates="agent")

    __table_args__ = (
        Index("idx_agent_kb", "kb_id"),
        Index("idx_agent_tenant", "tenant_id"),
    )


class AgentVersion(Base, TimestampMixin):
    """Agent 版本（@DDL-only）：每次发布固化版本快照。"""

    __tablename__ = "agent_version"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agent.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    graph_type: Mapped[str] = mapped_column(String(64), nullable=False)
    conf: Mapped[dict] = mapped_column(JSON, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    agent: Mapped[Agent] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("agent_id", "version_no", name="uk_agent_version_no"),
        Index("idx_agentver_tenant", "tenant_id"),
    )


class AgentTool(Base, TimestampMixin):
    """Agent 与 Tool 的绑定关系（@DDL-only）：一个 Agent 可绑定多个工具。"""

    __tablename__ = "agent_tool"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agent.id"), nullable=False)
    tool_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tool.id"), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("1"))
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    __table_args__ = (
        UniqueConstraint("agent_id", "tool_id", name="uk_agent_tool"),
        Index("idx_agenttool_tenant", "tenant_id"),
    )


class AgentKnowledge(Base, TimestampMixin):
    """Agent 与知识库的绑定关系（@DDL-only）。"""

    __tablename__ = "agent_knowledge"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agent.id"), nullable=False)
    kb_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_base.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("1"))
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    __table_args__ = (
        UniqueConstraint("agent_id", "kb_id", name="uk_agent_kb"),
        Index("idx_agentkb_tenant", "tenant_id"),
    )


class Model(Base, TimestampMixin):
    """模型实例（@DDL-）：绑定 provider 的具体模型。"""

    __tablename__ = "model"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_provider.id"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # JSON 编码的模型 meta（上下文窗口 / 单价 / 能力位点等）
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    provider: Mapped[ModelProvider] = relationship(back_populates="models")

    __table_args__ = (
        UniqueConstraint("provider_id", "model_name", name="uk_provider_model"),
        Index("idx_model_tenant", "tenant_id"),
    )


class ModelProvider(Base, TimestampMixin):
    """模型供应商（@DDL-only）：OpenAI / Anthropic / 本地 vLLM 等。"""

    __tablename__ = "model_provider"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)  # openai/anthropic/...
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 不存明文 key，仅落加密态的加密引用或掩码
    api_key_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    models: Mapped[list[Model]] = relationship(back_populates="provider")


class Tool(Base, TimestampMixin):
    """工具定义（@DDL-only）：HTTP / MCP / 内置函数统一描述。"""

    __tablename__ = "tool"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'http'"))
    # OpenAPI / JSON Schema 描述，供 Tool Gateway 校验入参
    params_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 敏感操作隔离：SQL/代码类必须 true
    isolated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    __table_args__ = (Index("idx_tool_tenant", "tenant_id"),)


class ToolPermission(Base, TimestampMixin):
    """工具调用授权（@DDL-only）：角色 → 工具，收口权限审计。"""

    __tablename__ = "tool_permission"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_role.id"), nullable=False)
    tool_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tool.id"), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'none'"))
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    __table_args__ = (
        UniqueConstraint("role_id", "tool_id", name="uk_role_tool"),
        Index("idx_toolperm_tenant", "tenant_id"),
    )


class AgentRun(Base, TimestampMixin):
    """Agent 运行时模型（@DDL-only）：与方案《Agent Runtime 统一 Run 模型》对齐。"""

    __tablename__ = "agent_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agent.id"), nullable=False)
    # 与 agent_version 版本号对齐
    agent_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    conv_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("conversation.id"), nullable=True
    )
    status: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), comment="RunStatus"
    )
    start_time: Mapped[datetime | None] = mapped_column(DateTime(3), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(3), nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    agent: Mapped[Agent] = relationship(back_populates="runs")
    events: Mapped[list[AgentEvent]] = relationship(back_populates="run")

    __table_args__ = (
        Index("idx_run_agent", "agent_id"),
        Index("idx_run_status", "status"),
        Index("idx_run_tenant", "tenant_id"),
    )


class AgentEvent(Base):
    """Agent 运行时事件流（@DDL-only）：`agent_run` 的多事件子表。

    与方案事件枚举对齐：run_start / node_start / llm_start / retrieval_start /
    tool_start / retry / error / run_end 等，记 one-to-many。
    """

    __tablename__ = "agent_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agent_run.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    node_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 事件负载 JSON（含耗时 / token / 错误等）
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(3),
        server_default=text("CURRENT_TIMESTAMP(3)"),
        nullable=False,
    )

    run: Mapped[AgentRun] = relationship(back_populates="events")

    __table_args__ = (
        Index("idx_event_run", "run_id"),
        Index("idx_event_type", "event_type"),
    )


class AuditLog(Base):
    """审计日志（@DDL-only）：敏感 / 变更动作留痕，只增不改。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # user / agent / system
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 变更前后快照（JSON），便于回溯
    diff_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(48), nullable=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(3),
        server_default=text("CURRENT_TIMESTAMP(3)"),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_audit_actor", "actor_type", "actor_id"),
        Index("idx_audit_tenant", "tenant_id"),
    )


__all__ = [
    "Agent",
    "AgentEvent",
    "AgentKnowledge",
    "AgentRun",
    "AgentStatus",
    "AgentTool",
    "AgentVersion",
    "AuditLog",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentStatus",
    "KnowledgeBase",
    "Message",
    "Model",
    "ModelProvider",
    "RunStatus",
    "SysPermission",
    "SysRole",
    "SysTenant",
    "SysUser",
    "Tool",
    "ToolPermission",
    "User",
]