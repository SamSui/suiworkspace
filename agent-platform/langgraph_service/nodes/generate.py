"""生成节点（增量 3 交付）。

把召回切片拼进 prompt 调用 LLM，产出答案与引用列表。
LLM 调用必须经 `langgraph_service/llm/client.py`（增量 3 落地）——
超时 / 退避重试 / 熔断 / 多 provider 兜底都在那一层，节点不重复实现。
"""

from __future__ import annotations

from typing import Any

from core.exceptions import NotImplementedYet
from langgraph_service.graph.state import AgentState

_INCREMENT = "增量 3（langgraph 编排）"


async def generate_node(state: AgentState) -> dict[str, Any]:
    raise NotImplementedYet(_INCREMENT, "generate 节点尚未实现")
