"""编排服务状态定义。

状态分层（设计文档 §1.2 / 裁决 #2）：
- 轻量元数据（user_id / kb_id / thread_id）随 checkpoint 走 Redis；
- 大文本（召回切片正文、对话长文）放 Redis 短会话缓存与 ES，**不进状态**——
  状态里只留 `chunk_id` 引用，避免 checkpoint 体积膨胀。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages

# `RetrievedChunk` 已收敛到 `core.retrieval`（P1.2 独立检索服务与编排共用同一类型，
# 避免两处重复定义漂移），此处重导出保持既有 `graph.state.RetrievedChunk` 导入兼容。
from core.retrieval import RetrievedChunk  # noqa: F401


class AgentState(TypedDict, total=False):
    """RAG 图的主状态。`total=False` 让节点只需返回自己改动的那部分。"""

    # --- 请求上下文 ---
    query: str
    user_id: int
    kb_id: str | None
    tenant_id: str | None  # P1.2：独立检索服务标量过滤入参（多租户隔离，P2 接入后生效）
    thread_id: str
    trace_id: str

    # --- 路由决策 ---
    need_retrieval: bool
    route: Literal["rag", "direct", "tool"]

    # --- 检索 ---
    retrieved: list[RetrievedChunk]

    # --- HITL（增量 3.6）---
    hitl_required: bool  # 命中需人工审批的场景则该节点先挂起
    hitl_verdict: str | None  # resume 后的人工决策（approve / reject）
    aborted: bool  # 人工取消后置位，generate 据此短路不再调 LLM

    # --- 生成 ---
    answer: str
    citations: list[str]
    usage: dict[str, Any]
    llm_provider: str | None  # 本趟实际命中的 provider（观测用）

    # --- 对话历史（LangGraph 内置 reducer，按消息追加而非覆盖）---
    messages: Annotated[list[Any], add_messages]
