"""审计写入（P1.3 · SUIG-33）。

审计是网关的必交能力：LLM 调用 / 工具调用每次都要落审计，未授权调用同样落审计
（拒也留痕）。写盘不与业务同事务——审计失败绝不影响主链路（只告警、不抛错）。

两张目标表契约对齐 P1.1（SUIG-31 `01_schema.sql`）：
- `audit_log`：actor_type / actor_id / action / resource_type / resource_id /
  diff_context / ip / tenant_id / created_at（只增、DDL-only）；
- `agent_run`：run_id / agent_id / agent_version / conv_id / status /
  start_time / end_time / token_usage / error_code / error_message / tenant_id。

`agent_run` 依赖 agent 行存在（NOT NULL FK）。网关在不知道既有 agent_id 的通用
场景下默认只落 `audit_log`；`agent_run` 由具备 `agent_id` 上下文的编排方启用。
两个写入器都遵循“后端不可用即降级为内存、不抛给调用方”的稳健语义。
"""

from __future__ import annotations

import dataclasses
import datetime
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AuditRecord:
    """一次审计事件的规范化结构（写入后端之前的中间表示）。"""

    actor_type: str = "system"
    actor_id: str = "llm-gateway"
    action: str = ""
    resource_type: str | None = None
    resource_id: str | None = None
    diff_context: dict[str, Any] | None = None
    ip: str | None = None
    tenant_id: int = 0
    created_at: datetime.datetime = dataclasses.field(
        default_factory=datetime.datetime.now
    )
    # agent_run 专用：若后端支持且调用方提供 agent_id，则同时落 run 行
    agent_run_id: str | None = None
    agent_id: int | None = None
    agent_version: int = 1
    run_status: int = 0  # RunStatus：0 创建 / 1 成功 / 2 失败

    @property
    def diff(self) -> dict[str, Any]:
        """diff_context 的快捷只读访问（可能为 None）。"""
        return self.diff_context or {}

    def as_audit_log_kwargs(self) -> dict[str, Any]:
        """映射到 `audit_log` 表 INSERT 的字段。"""
        return {
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "diff_context": self.diff_context,
            "ip": self.ip,
            "tenant_id": self.tenant_id,
        }

    def as_agent_run_kwargs(self) -> dict[str, Any] | None:
        """映射到 `agent_run` 表 INSERT 的字段；无 agent_id 则返回 None（不写 run 行）。"""
        if self.agent_id is None:
            return None
        d = self.diff
        err_code = d.get("error_code")
        err_msg = d.get("error_message")
        return {
            "run_id": self.agent_run_id or (self.resource_id or ""),
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "conv_id": d.get("conv_id"),
            "status": self.run_status,
            "start_time": self.created_at,
            "end_time": self.created_at,
            "token_usage": d.get("usage"),
            "error_code": err_code,
            "error_message": err_msg,
            "tenant_id": self.tenant_id,
        }


class AuditWriter(Protocol):
    """审计后端的抽象接口。实现须为线程安全。"""

    def append(self, record: AuditRecord) -> None:
        """持久化一条审计事件。实现内部处理失败降级，不得向调用方抛错。"""
        ...


class MemoryAuditBackend:
    """进程内内存后端。用于单测 / 本机调试（无 DB 依赖）。线程安全。"""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._lock = threading.Lock()

    def append(self, record: AuditRecord) -> None:
        with self._lock:
            self._records.append(record)

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        with self._lock:
            return tuple(self._records)


class MySQLAuditBackend:
    """MySQL 后端：写 `audit_log`（必），`agent_run`（具备 agent_id 时）。

    依赖 `core.storage.StorageContainer.mysql`（SQLAlchemy 异步会话）。写失败一律
    降级：仅告警 + 回落到内存兜底，绝不让审计异常影响调用主路径。
    """

    def __init__(  # 构造：容器 + 可选内存兜底
        self, container: Any, *, memory_fallback: MemoryAuditBackend | None = None
    ) -> None:
        self._container = container
        self._mem = memory_fallback or MemoryAuditBackend()

    def append(self, record: AuditRecord) -> None:
        # 先落内存快照（可回溯 / 降级用）
        self._mem.append(record)
        try:
            self._persist(record)
        except Exception as exc:  # noqa: BLE001 — 审计失败绝不向上抛
            logger.warning(
                "audit persist failed, kept in memory",
                extra={"extra_fields": {"error": str(exc), "action": record.action}},
            )

    async def _persist(self, record: AuditRecord) -> None:
        from sqlalchemy import text

        mysql = getattr(self._container, "mysql", None)
        if mysql is None:
            return
        async with mysql.session() as session:
            run_kwargs = record.as_agent_run_kwargs()
            if run_kwargs is not None:
                try:
                    await session.execute(
                        text(
                            "INSERT INTO agent_run "
                            "(`run_id`,`agent_id`,`agent_version`,`conv_id`,`status`,"
                            "`start_time`,`end_time`,`token_usage`,`error_code`,"
                            "`error_message`,`tenant_id`) "
                            "VALUES (:run_id,:agent_id,:agent_version,:conv_id,:status,"
                            ":start_time,:end_time,:token_usage,:error_code,"
                            ":error_message,:tenant_id)"
                        ),
                        run_kwargs,
                    )
                except Exception as exc:  # noqa: BLE001 — run 行写失败不阻断 audit_log
                    logger.warning(
                        "agent_run insert skipped",
                        extra={"extra_fields": {"error": str(exc)}},
                    )
            await session.execute(
                text(
                    "INSERT INTO audit_log "
                    "(actor_type,actor_id,action,resource_type,resource_id,diff_context,"
                    "ip,tenant_id,created_at) "
                    "VALUES (:actor_type,:actor_id,:action,:resource_type,:resource_id,"
                    ":diff_context,:ip,:tenant_id,:created_at)"
                ),
                {
                    **record.as_audit_log_kwargs(),
                    "created_at": record.created_at,
                },
            )


def build_audit_writer(backend: str, container: Any) -> AuditWriter:
    """按配置构造审计后端。`memory` 用内存；`mysql` 用 MySQL（失败回落内存）。"""
    if backend == "mysql":
        return MySQLAuditBackend(container)
    return MemoryAuditBackend()


__all__ = [
    "AuditRecord",
    "AuditWriter",
    "MemoryAuditBackend",
    "MySQLAuditBackend",
    "build_audit_writer",
]