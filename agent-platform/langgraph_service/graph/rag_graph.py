"""RAG 图装配（增量 3 交付）。

图形状：
    router ──need_retrieval=true──> retrieve ──> hitl_gate ──> generate ──> END
      └──need_retrieval=false──────────────> hitl_gate ──> generate ──> END

`hitl_gate` 仅当 state.hitl_required 时才挂起，否则放行（无 HITL 场景零感知）。

节点依赖（container / settings / llm_client / emit）不在编译期绑定，而是由调用方
经 `config["configurable"]["deps"]` 注入——`build_rag_graph` 只做拓扑 + checkpointer。
"""

from __future__ import annotations

from typing import Any

from core.exceptions import NotImplementedYet
from core.logging import get_logger
from langgraph_service.graph.state import AgentState

logger = get_logger(__name__)

_INCREMENT = "增量 3（langgraph 编排）"


def _route_after_router(state: AgentState) -> str:
    """条件分支：需要检索走 retrieve，否则直接生成（仍经 HITL 门）。"""
    return "retrieve" if state.get("need_retrieval") else "hitl_gate"


def build_rag_graph(checkpointer: Any) -> Any:
    """编译 RAG 图。checkpointer 必须传入（RedisSaver）——不传退化为内存态，多实例不共享。

    延迟导入 langgraph —— 未安装 SDK 的环境（如只跑网关的部署）不应因此导入失败。
    """
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:  # pragma: no cover
        raise NotImplementedYet(_INCREMENT, "缺少 langgraph，请安装后重试") from exc

    from langgraph_service.nodes.generate import generate_node
    from langgraph_service.nodes.hitl import hitl_gate
    from langgraph_service.nodes.retrieve import retrieve_node
    from langgraph_service.nodes.router import router_node

    graph = StateGraph(AgentState)
    graph.add_node("router", router_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("hitl_gate", hitl_gate)
    graph.add_node("generate", generate_node)

    graph.set_entry_point("router")
    graph.add_conditional_edges(
        "router",
        _route_after_router,
        {"retrieve": "retrieve", "hitl_gate": "hitl_gate"},
    )
    graph.add_edge("retrieve", "hitl_gate")
    graph.add_edge("hitl_gate", "generate")
    graph.add_edge("generate", END)

    # checkpointer 必须传入（RedisSaver）——不传则退化为内存态，多实例不共享
    return graph.compile(checkpointer=checkpointer)