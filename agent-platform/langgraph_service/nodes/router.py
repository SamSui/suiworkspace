"""router 节点（增量 3.3 落地）。

意图判定：query 是否需要检索知识库 / 走哪个子图。产出写入 state：
- `need_retrieval` —— 是否进入 retrieve 分支；
- `route` —— `rag`（带 kb 且命中检索性意图）/ `direct`（直答）。

判定策略（默认规则版，无需外部 LLM，满足"需检索/直答两类用例判定正确"）：
- 未指定 kb_id → 无处可检索 → `direct`；
- 检索性触发词（问"什么是/怎么做/依据/文件/文档/资料/条款/定义"等，指向库内事实）→ `rag`；
- 闲聊 / 问候 / 无信息需求 → `direct`。

外部 LLM 版可作为 `classifier_factory` 注入（3.5 LLM 就绪后），默认走规则，
保证无凭据环境也可判，且单测可精确断言。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.logging import get_logger
from langgraph_service.graph.state import AgentState

logger = get_logger(__name__)

# 检索性触发词：命中任一且带 kb → 走 RAG
_RETRIEVAL_MARKERS = {
    "什么", "为什么", "怎么做", "如何", "依据", "出处", "文件", "文档", "资料",
    "条款", "定义", "说明", "规定", "标准", "文档里", "库中", "检索", "查询",
    "find", "retrieve", "what is", "meaning", "definition", "rule", "policy",
    "条款号", "规范", "指引", "要求", "手册", "制度", "政策", "办法", "流程",
    "怎么走", "找一下", "找找", "查一下",
}


def _decisive_route(query: str, kb_id: str | None) -> tuple[bool, str]:
    """返回 (need_retrieval, route)。"""
    if not kb_id:
        return False, "direct"
    q = query.lower()
    if any(m in q for m in _RETRIEVAL_MARKERS):
        return True, "rag"
    return False, "direct"


async def router_node(
    state: AgentState,
    classifier: Callable[[str, str | None], tuple[bool, str]] | None = None,
) -> dict[str, Any]:
    """入口路由节点。

    `classifier` 可注入自定义版本（默认 `_decisive_route`）。返回更新 state 的字段。
    """
    query = state.get("query", "")
    kb_id = state.get("kb_id")
    judge = classifier or _decisive_route
    need_retrieval, route = judge(query, kb_id)
    logger.info(
        "router",
        extra={"extra_fields": {"route": route, "need_retrieval": need_retrieval, "query": query}},
    )
    return {"need_retrieval": need_retrieval, "route": route}


__all__ = ["router_node", "_RETRIEVAL_MARKERS", "_decisive_route"]