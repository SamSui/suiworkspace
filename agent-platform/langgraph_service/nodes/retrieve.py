"""检索节点（增量 3 交付）。

落地设计文档 §4 的混合检索三段式：
1. 并发召回：Milvus 向量（按 kb_id 过滤）+ ES 关键词；
2. 融合去重；
3. BGE-Reranker 重排取 top-K；
4. 命中 Redis 检索缓存则直接返回（跳过 1–3）。

依赖注入的容器由调用方通过 partial / config 传入，节点本身不持有全局状态。
"""

from __future__ import annotations

from typing import Any

from core.exceptions import NotImplementedYet
from langgraph_service.graph.state import AgentState

_INCREMENT = "增量 3（langgraph 编排）"


async def retrieve_node(state: AgentState) -> dict[str, Any]:
    raise NotImplementedYet(_INCREMENT, "retrieve 节点尚未实现")
