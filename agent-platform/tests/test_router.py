"""3.3 router 节点单测。

验收点：需检索 / 直答两类用例判定正确。
"""

from __future__ import annotations

from langgraph_service.nodes.router import _decisive_route, router_node


async def test_direct_when_no_kb():
    need, route = _decisive_route("你好", None)
    assert not need and route == "direct"


async def test_retrieval_trigger_with_kb():
    need, route = _decisive_route("报销流程是什么", "kb-1")
    assert need and route == "rag"


async def test_greeting_direct_even_with_kb():
    need, route = _decisive_route("在吗", "kb-1")
    assert not need and route == "direct"


async def test_route_queried_via_node():
    upd = await router_node({"query": "员工手册找一下", "kb_id": "kb-9"})
    assert upd["need_retrieval"] is True and upd["route"] == "rag"


async def test_custom_classifier_injected():
    def custom(_q, _kb):
        return True, "tool"

    upd = await router_node({"query": "x", "kb_id": None}, classifier=custom)
    assert upd["need_retrieval"] is True and upd["route"] == "tool"