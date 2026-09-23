"""3.5 SSE 编码 + generate 节点单测。

验收点：
- SSE 事件符合冻结契约（token/interrupt/done/error + 心跳，seq 单调）；
- generate 输出引用可回链 chunk_id。
"""

from __future__ import annotations

from langgraph_service.sse import SSEEncoder


def test_sse_token_frame_shape():
    enc = SSEEncoder()
    frame = enc.token("好")
    assert frame.startswith("event: token\n")
    assert '"text": "好"' in frame
    assert '"seq": 1' in frame


def test_sse_done_frame():
    enc = SSEEncoder()
    frame = enc.done(message_id=7, usage={"prompt": 10, "completion": 3})
    assert frame.startswith("event: done\n")
    assert '"message_id": 7' in frame
    assert '"usage"' in frame


def test_sse_interrupt_frame():
    enc = SSEEncoder()
    frame = enc.interrupt(reason="human_approval", payload={"p": 1})
    assert frame.startswith("event: interrupt\n")
    assert '"reason": "human_approval"' in frame


def test_sse_error_frame():
    enc = SSEEncoder()
    frame = enc.error(code="llm_timeout", message="boom", trace_id="t1")
    assert frame.startswith("event: error\n")
    assert '"code": "llm_timeout"' in frame and '"trace_id": "t1"' in frame


def test_seq_is_monotonic():
    enc = SSEEncoder()
    f1 = enc.token("a")
    f2 = enc.interrupt()
    f3 = enc.done(message_id=1, usage={})
    assert '"seq": 1' in f1 and '"seq": 2' in f2 and '"seq": 3' in f3


def test_heartbeat():
    enc = SSEEncoder()
    assert enc.heartbeat() == ": ping\n\n"

    # 未到阈值不发心跳，跨阈值发
    assert enc.maybe_heartbeat(now=0.1) == ""
    assert enc.maybe_heartbeat(now=11.0) != ""


async def test_generate_citations_link_to_chunk_id():
    """generate 的 citations 必须来自 retrieved 的 chunk_id（引用可回链）。"""
    from langgraph_service.nodes.generate import generate_node

    emitted: list[str] = []

    class _LLM:
        async def stream(self, messages, **kw):
            # 模拟 LLM 输出包含引用标记
            yield "公司报销规则见"
            yield "文档"
        last_provider = "echo"

    retrieved = [
        {
            "chunk_id": "ck-1",
            "doc_id": "d1",
            "kb_id": "k1",
            "score": 0.8,
            "source": "vector",
            "highlight": "报销规则",
        },
        {
            "chunk_id": "ck-2",
            "doc_id": "d1",
            "kb_id": "k1",
            "score": 0.5,
            "source": "keyword",
            "highlight": "流程",
        },
    ]

    class _ES:
        async def fetch_chunks(self, ids):
            return [
                {"chunk_id": "ck-1", "text": "报销规则见第3条"},
                {"chunk_id": "ck-2", "text": "流程需提前申请"},
            ]

    container = type("C", (), {"es": _ES()})()
    deps = {"container": container, "llm_client": _LLM(), "emit": lambda f: emitted.append(f)}
    cfg = {"configurable": {"deps": deps}}
    upd = await generate_node({"query": "报销怎么走", "retrieved": retrieved}, cfg)

    assert set(upd["citations"]) == {"ck-1", "ck-2"}  # 引用可回链 chunk_id
    assert "ck-1" in upd["citations"]
    assert len(emitted) > 0  # 流式 token 已外发
    assert upd["answer"]
    assert upd["usage"]["completion"] > 0