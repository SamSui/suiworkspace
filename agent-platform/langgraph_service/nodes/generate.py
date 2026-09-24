"""generate 节点（增量 3.5 落地）。

把召回切片（经 ES 水合正文）拼进 prompt 调 LLM，流式产出答案与引用列表：
- LLM 调用必须经 `llm/client.py`（超时/退避/熔断/兜底已在那一层）。
- 逐 token 经 deps 提供的 SSE emitter 即时推给下游（首 token 时延达标见 3.1 埋点）。
- 引用 `[N]` 可回链 `chunk_id`（state 只留引用，正文水合不写回 checkpoint）。

注意：本模块**不使用** `from __future__ import annotations`——langgraph 1.2 靠运行时
注解识别节点的 `config` 形参（字符串注解识别不到，会导致 deps 注入失效）。见
`retrieve.py` 同注。
"""

from typing import Any

from langgraph.types import RunnableConfig

from core.logging import get_logger
from langgraph_service.graph.state import AgentState
from langgraph_service.retrieval import text_hydrate

logger = get_logger(__name__)


def _build_system_prompt() -> str:
    return (
        "你是智能体中台的问答助手。请基于给定的知识切片回答；回答须标注引用"
        "（形如 [1][2]），并确保每个引用都能对应到切片编号。若无相关切片则明说"
        "『未在当前知识库中找到依据』，不要编造。"
    )


def _build_user_prompt(query: str, chunks: list[dict[str, str]]) -> str:
    """拼接引用切片。`chunks` 为 [{ref, text, highlight}]，按编号排列。"""
    lines: list[str] = [f"问题：{query}", "", "参考资料："]
    if not chunks:
        lines.append("（无可用切片）")
    for i, c in enumerate(chunks, start=1):
        text = c.get("text") or c.get("highlight") or ""
        lines.append(f"[{i}] {text}")
    return "\n".join(lines)


async def generate_node(
    state: AgentState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    deps = (config or {}).get("configurable", {}).get("deps") or {}
    llm_client = deps.get("llm_client")
    container = deps.get("container")
    emit = deps.get("emit")  # callable(dict) -> None; None = 非流式（收敛）

    query = state.get("query", "")
    retrieved = state.get("retrieved") or []

    # 人工取消短路：不调 LLM，返回既有/空结果
    if state.get("aborted"):
        logger.info("generate aborted by human", extra={"extra_fields": {"query": query}})
        return {"answer": state.get("answer", "已取消回复。"), "citations": [], "usage": {}}

    # 水合切片正文（文本不进 checkpoint；取不到回落 highlight）
    es = getattr(container, "es", None) if container else None
    material = await text_hydrate(es, retrieved)
    chunk_texts: list[dict[str, str]] = []
    for c in retrieved:
        cid = c["chunk_id"]
        text, highlight = material.get(cid, ("", ""))
        chunk_texts.append({"ref": cid, "text": text, "highlight": highlight})

    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": _build_user_prompt(query, chunk_texts)},
    ]

    usage: dict[str, Any] = {}
    citations: list[str] = [c["chunk_id"] for c in retrieved][: len(chunk_texts)]
    pieces: list[str] = []

    # 流式：边收 token 边经 emitter 外发；provider 归因取 client 记录
    async for token in llm_client.stream(messages, metadata={"query": query}):
        pieces.append(token)
        if emit is not None:
            emit({"text": token})

    answer = "".join(pieces)
    usage = {"completion": len(answer), "prompt": len(_build_user_prompt(query, chunk_texts))}
    llm_provider = getattr(llm_client, "last_provider", None)

    return {
        "answer": answer,
        "citations": citations,
        "usage": usage,
        "llm_provider": llm_provider,
        "hitl_verdict": state.get("hitl_verdict"),
    }


__all__ = ["generate_node"]