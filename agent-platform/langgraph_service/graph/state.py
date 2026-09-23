"""编排服务状态定义。

状态分层（设计文档 §1.2 / 裁决 #2）：
- 轻量元数据（user_id / kb_id / thread_id）随 checkpoint 走 Redis；
- 大文本（召回切片正文、对话长文）放 Redis 短会话缓存与 ES，**不进状态**——
  状态里只留 `chunk_id` 引用，避免 checkpoint 体积膨胀。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages


class RetrievedChunk(TypedDict):
    """一条召回结果。只带引用与分数，正文按需回查 ES。"""

    chunk_id: str
    doc_id: str
    kb_id: str
    score: float
    source: Literal["vector", "keyword", "fused"]
    highlight: str | None


class AgentState(TypedDict, total=False):
    """RAG 图的主状态。`total=False` 让节点只需返回自己改动的那部分。"""

    # --- 请求上下文 ---
    query: str
    user_id: int
    kb_id: str | None
    thread_id: str
    trace_id: str

    # --- 路由决策 ---
    need_retrieval: bool
    route: Literal["rag", "direct", "tool"]

    # --- 检索 ---
    retrieved: list[RetrievedChunk]

    # --- 生成 ---
    answer: str
    citations: list[str]
    usage: dict[str, Any]

    # --- 对话历史（LangGraph 内置 reducer，按消息追加而非覆盖）---
    messages: Annotated[list[Any], add_messages]
