"""3.6 HITL 挂起/恢复单测（图级）。

用真实 checkpointer（先 InMemory，保证逻辑正确；Redis 真跑见 `test_checkpointer_multiprocess.py`
与 scripts/pressure_dual.py 的进程级验证）。验收：挂起 → 恢复后可续跑（generate 产出）。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from langgraph_service.graph.rag_graph import build_rag_graph


class _LLM:
    last_provider = "echo"

    async def stream(self, messages, **kw):
        for t in ("已生成", "答案"):
            yield t


class _Container:
    es = None


def _deps():
    from core.config import get_settings

    return {
        "container": _Container(),
        "settings": get_settings(),
        "llm_client": _LLM(),
        "emit": None,
    }


def _cfg(thread_id):
    cfg = {"configurable": {"thread_id": thread_id, "deps": _deps()}}
    return cfg


async def test_hitl_blocks_then_resumes():
    app = build_rag_graph(checkpointer=InMemorySaver())
    thread = "hitl-t-1"
    cfg = _cfg(thread)

    # 首跑：hitl_required=True → 应在 generate 前挂起，无 done/answer
    updates = []
    async for chunk in app.astream(
        {"query": "报销流程是什么", "kb_id": "k1", "thread_id": thread, "hitl_required": True},
        cfg,
        stream_mode="updates",
    ):
        updates.append(chunk)
    assert any("__interrupt__" in c for c in updates)
    assert not any("generate" in c for c in updates)  # 未走到 generate

    # resume approve → 挂起解除，续跑 generate
    resumed = []
    async for chunk in app.astream(
        Command(resume="approve"), cfg, stream_mode="updates"
    ):
        resumed.append(chunk)
    assert any("generate" in c for c in resumed)


async def test_hitl_skipped_when_not_required():
    app = build_rag_graph(checkpointer=InMemorySaver())
    updates = []
    async for chunk in app.astream(
        {"query": "你好", "kb_id": None, "thread_id": "t-nohitl", "hitl_required": False},
        _cfg("t-nohitl"),
        stream_mode="updates",
    ):
        updates.append(chunk)
    # 无 interrupt，且走到了 generate
    assert not any("__interrupt__" in c for c in updates)
    assert any("generate" in c for c in updates)


async def test_hitl_reject_aborts_generate():
    app = build_rag_graph(checkpointer=InMemorySaver())
    thread = "hitl-t-reject"
    cfg = _cfg(thread)
    async for _ in app.astream(
        {"query": "提交付款申请", "kb_id": "k1", "thread_id": thread, "hitl_required": True},
        cfg,
        stream_mode="updates",
    ):
        pass
    resumed = []
    async for chunk in app.astream(Command(resume="reject"), cfg, stream_mode="updates"):
        resumed.append(chunk)
    # generate 短路，answer 为拦截文案
    assert any("generate" in c for c in resumed)  # generate 节点仍被调用（短路返回）