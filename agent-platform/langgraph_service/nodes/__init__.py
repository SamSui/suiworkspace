"""编排节点（增量 3 交付）。

节点契约：入参是当前 `AgentState`，返回**只含改动字段**的 dict；
重活（LLM / 检索）必须 async，且带超时与重试。
"""

from langgraph_service.nodes.generate import generate_node
from langgraph_service.nodes.hitl import hitl_gate
from langgraph_service.nodes.retrieve import retrieve_node
from langgraph_service.nodes.router import router_node

__all__ = ["generate_node", "hitl_gate", "retrieve_node", "router_node"]
