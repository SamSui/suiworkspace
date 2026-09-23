"""RAG 图装配（增量 3 交付）。

骨架已给出图的形状：router ─┬─> retrieve ─> generate
                            └─> generate（无需检索时直答）

节点实现见 `langgraph_service/nodes/`，当前为占位（增量 3）。
"""

from __future__ import annotations

from typing import Any

from core.exceptions import NotImplementedYet
from core.logging import get_logger
from langgraph_service.graph.state import AgentState

logger = get_logger(__name__)

_INCREMENT = "增量 3（langgraph 编排）"


def _route_after_router(state: AgentState) -> str:
    """条件分支：需要检索走 retrieve，否则直接生成。"""
    return "retrieve" if state.get("need_retrieval") else "generate"


def build_rag_graph(checkpointer: Any) -> Any:
    """编译 RAG 图。

    延迟导入 langgraph —— 未安装 SDK 的环境（如只跑网关的部署）不应因此导入失败。
    """
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:  # pragma: no cover
        raise NotImplementedYet(_INCREMENT, "缺少 langgraph，请安装后重试") from exc

    from langgraph_service.nodes.generate import generate_node
    from langgraph_service.nodes.retrieve import retrieve_node
    from langgraph_service.nodes.router import router_node

    graph = StateGraph(AgentState)
    graph.add_node("router", router_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)

    graph.set_entry_point("router")
    graph.add_conditional_edges(
        "router",
        _route_after_router,
        {"retrieve": "retrieve", "generate": "generate"},
    )
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)

    # checkpointer 必须传入（RedisSaver）——不传则退化为内存态，多实例不共享
    return graph.compile(checkpointer=checkpointer)
