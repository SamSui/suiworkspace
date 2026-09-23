"""路由节点（增量 3 交付）。

判定：是否需要检索知识库 / 走哪个子图。产出写入 state 的 `need_retrieval` 与 `route`，
由 `rag_graph._route_after_router` 消费。
"""

from __future__ import annotations

from typing import Any

from core.exceptions import NotImplementedYet
from langgraph_service.graph.state import AgentState

_INCREMENT = "增量 3（langgraph 编排）"


async def router_node(state: AgentState) -> dict[str, Any]:
    raise NotImplementedYet(_INCREMENT, "router 节点尚未实现")
