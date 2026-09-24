"""Agent 配置路由（增量 2.5：查询 + 新增）。

每个 Agent 配置绑定一个 kb；任何读写都必须先过 `require_kb_access`（裁决 #4）。
`list` 与 `create` 都以 `kb_id` 维度授权：不存在/越权统一 404，不泄漏存在性。
创建默认 `version=1, status=1`；版本字段为灰度留白（增量 3 编排消费）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.deps import current_user, get_container, require_kb_access
from db.models import AgentConfig

router = APIRouter(prefix="/v1/agent", tags=["agent"])


class AgentConfigCreate(BaseModel):
    kb_id: int
    name: str = Field(min_length=1, max_length=128)
    graph_type: str = Field(description="rag_graph / tool_graph / ...")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_k: int = Field(default=5, ge=1, le=50)
    conf: dict[str, Any] = Field(default_factory=dict)


class AgentConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int
    name: str
    graph_type: str
    temperature: float
    top_k: int
    conf: dict[str, Any]
    version: int
    status: int


def _config_to_out(cfg: AgentConfig) -> AgentConfigOut:
    return AgentConfigOut(
        id=int(cfg.id),
        kb_id=int(cfg.kb_id),
        name=cfg.name,
        graph_type=cfg.graph_type,
        temperature=float(cfg.temperature),
        top_k=cfg.top_k,
        conf=cfg.conf or {},
        version=cfg.version,
        status=cfg.status,
    )


@router.get("", response_model=list[AgentConfigOut])
async def list_agent_configs(
    kb_id: int,
    user=Depends(current_user),
    container=Depends(get_container),
) -> list[AgentConfigOut]:
    """列出某个知识库下当前用户的 Agent 配置。"""
    async with container.mysql.session() as session:
        await require_kb_access(kb_id, user, session)
        rows = await session.execute(
            select(AgentConfig)
            .where(AgentConfig.kb_id == kb_id, AgentConfig.status == 1)
            .order_by(AgentConfig.id)
        )
        return [_config_to_out(c) for c in rows.scalars().all()]


@router.post("", response_model=AgentConfigOut, status_code=201)
async def create_agent_config(
    payload: AgentConfigCreate,
    user=Depends(current_user),
    container=Depends(get_container),
) -> AgentConfigOut:
    """新建 Agent 配置（默认 `version=1, status=1`）。"""
    async with container.mysql.session() as session:
        await require_kb_access(payload.kb_id, user, session)
        cfg = AgentConfig(
            kb_id=payload.kb_id,
            name=payload.name,
            graph_type=payload.graph_type,
            temperature=payload.temperature,
            top_k=payload.top_k,
            conf=payload.conf,
            version=1,
            status=1,
        )
        session.add(cfg)
        # server_default(version/status 由显式赋值覆盖；刷新取回 id 等列)
        await session.flush()
        await session.refresh(cfg)
        return _config_to_out(cfg)