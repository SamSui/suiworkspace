"""数据模型纯单测（SUIG-31）：21 表 ORM 映射自洽。

不依赖数据库：`Base.metadata` 与 mapper 配置是进程内操作。本用例守护
验收口径第一条的 ORM 侧等价物——21 表均在 metadata 注册、表名不重复、
back_populates 关系成对（配置期不抛错）。

注意：跨模型的正反向 relationship 若有一侧未定义，SQLAlchemy 会在
mapper 配置（即本测试首次触碰 metadata）时报错——故用 `configure_mappers()`
强制解析，等同「一次全量 CREATE 前的最小自检」。
"""

from __future__ import annotations

import db.models as models
from db.base import Base

# 21 表集合（V1.0 收敛，SUIG-31）
EXPECTED_21 = {
    "user",
    "knowledge_base",
    "document",
    "conversation",
    "message",
    "agent",
    "sys_tenant",
    "sys_user",
    "sys_role",
    "sys_permission",
    "agent_version",
    "agent_tool",
    "agent_knowledge",
    "document_chunk",
    "model",
    "model_provider",
    "tool",
    "tool_permission",
    "agent_run",
    "agent_event",
    "audit_log",
}


def test_metadata_registers_exactly_21_tables() -> None:
    # 导入 db.models 会注册全部实体；再强制解析所有 mapper，
    # 任何 relationship 成对性 / 命名缺失都会在此抛错。
    from sqlalchemy.orm import configure_mappers

    configure_mappers()

    tables = set(Base.metadata.tables)
    assert tables == EXPECTED_21, (
        f"表集合与 V1.0 21 表不一致: 多= {tables - EXPECTED_21}, 少= {EXPECTED_21 - tables}"
    )
    assert len(tables) == 21


def test_agent_config_renamed_to_agent() -> None:
    """`agent_config` 已收敛为 `agent`，不再占位；数据迁移由 SQL 脚本承担。"""
    tables = set(Base.metadata.tables)
    assert "agent" in tables
    assert "agent_config" not in tables


def test_legacy_table_names_unchanged() -> None:
    """既有 5 表名保持不变，避免破坏既有查询 / 单测。"""
    tables = set(Base.metadata.tables)
    for name in ("user", "knowledge_base", "document", "conversation", "message"):
        assert name in tables


def test_entity_exports_all_21() -> None:
    """导出面与 21 表一一对应（含枚举 AgentStatus / RunStatus / DocumentStatus）。"""
    export = set(models.__all__)
    expected_entities = {
        "User",
        "Conversation",
        "KnowledgeBase",
        "Document",
        "Message",
        "Agent",
        "SysTenant",
        "SysUser",
        "SysRole",
        "SysPermission",
        "AgentVersion",
        "AgentTool",
        "AgentKnowledge",
        "DocumentChunk",
        "Model",
        "ModelProvider",
        "Tool",
        "ToolPermission",
        "AgentRun",
        "AgentEvent",
        "AuditLog",
        "AgentStatus",
        "RunStatus",
        "DocumentStatus",
    }
    assert expected_entities <= export