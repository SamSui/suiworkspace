"""SUIG-39 引用事件透出（SSE `cite`）单测。

验收点：
- `SSEEncoder.cite` 产出符合向后兼容的 `event: cite` 帧（ref/chunk_id/doc_id/kb_id）；
- `generate` 节点在流式启用时、token 流之前逐条 emit 引用事件（ref=1..N）；
- 非流式（emit=None）不 emit 引用事件；
- 网关 `_dispatch_emit` 把 `cite` 帧路由到 cite 编码、其余按 token 转发。
"""

from __future__ import annotations

import pytest

from langgraph_service.sse import SSEEncoder


def test_sse_cite_frame_shape() -> None:
    enc = SSEEncoder()
    frame = enc.cite(ref=1, chunk_id="ck-1", doc_id="d1", kb_id="k1")
    assert frame.startswith("event: cite\n")
    assert '"ref": 1' in frame
    assert '"chunk_id": "ck-1"' in frame
    assert '"doc_id": "d1"' in frame
    assert '"kb_id": "k1"' in frame


def test_cite_seq_increments_with_token() -> None:
    enc = SSEEncoder()
    f_cite = enc.cite(ref=1, chunk_id="c", doc_id="d", kb_id="k")
    f_tok = enc.token("你好")
    assert '"seq": 1' in f_cite
    assert '"seq": 2' in f_tok


def test_dispatch_emit_routes_cite_frame() -> None:
    from langgraph_service.main import _dispatch_emit

    queue: "asyncio.Queue[str]" = __import__("asyncio").Queue()
    enc = SSEEncoder()
    _dispatch_emit(
        enc,
        queue,
        {"event": "cite", "cite": {"ref": 2, "chunk_id": "c2", "doc_id": "d2", "kb_id": "k2"}},
    )
    _dispatch_emit(enc, queue, {"text": "好"})

    cite_frame = queue.get_nowait()
    tok_frame = queue.get_nowait()
    assert cite_frame.startswith("event: cite\n")
    assert '"ref": 2' in cite_frame
    assert tok_frame.startswith("event: token\n")
    assert '"text": "好"' in tok_frame


async def test_generate_emits_cite_before_tokens() -> None:
    """流式下 generate 先 emit 每条引用的 cite 帧，再逐 token 透出。"""
    from langgraph_service.nodes.generate import generate_node

    frames: list[dict] = []

    class _LLM:
        last_provider = "echo"

        async def stream(self, messages, **kw):
            yield "报销见"
            yield "条款"

    class _ES:
        async def fetch_chunks(self, ids):
            return [{"chunk_id": "ck-1", "text": "A"}, {"chunk_id": "ck-2", "text": "B"}]

    container = type("C", (), {"es": _ES()})()
    retrieved = [
        {"chunk_id": "ck-1", "doc_id": "d1", "kb_id": "k1", "score": 0.8,
         "source": "vector", "highlight": "A"},
        {"chunk_id": "ck-2", "doc_id": "d1", "kb_id": "k1", "score": 0.5,
         "source": "keyword", "highlight": "B"},
    ]
    deps = {"container": container, "llm_client": _LLM(), "emit": frames.append}
    cfg = {"configurable": {"deps": deps}}

    upd = await generate_node({"query": "q", "retrieved": retrieved}, cfg)

    cite_frames = [f for f in frames if f.get("event") == "cite"]
    assert len(cite_frames) == 2
    assert cite_frames[0]["cite"]["ref"] == 1
    assert cite_frames[0]["cite"]["chunk_id"] == "ck-1"
    assert cite_frames[1]["cite"]["chunk_id"] == "ck-2"
    # cite 一律排在第一条 token 之前
    events = [f.get("event") or "token" for f in frames]
    assert events[:2] == ["cite", "cite"]
    # 引用可回链
    assert set(upd["citations"]) == {"ck-1", "ck-2"}
    assert upd["answer"] == "报销见条款"


async def test_generate_non_stream_no_cite() -> None:
    """非流式（emit 为 None）不得 emit 引用事件，也不动 token。"""
    from langgraph_service.nodes.generate import generate_node

    class _LLM:
        async def stream(self, messages, **kw):
            yield "答案"
        last_provider = "echo"

    class _ES:
        async def fetch_chunks(self, ids):
            return [{"chunk_id": "ck-1", "text": "T"}]

    container = type("C", (), {"es": _ES()})()
    retrieved = [{"chunk_id": "ck-1", "doc_id": "d1", "kb_id": "k1",
                  "score": 0.7, "source": "vector", "highlight": None}]
    cfg = {"configurable": {"deps": {"container": container, "llm_client": _LLM(),
                                     "emit": None}}}
    upd = await generate_node({"query": "q", "retrieved": retrieved}, cfg)

    assert upd["answer"] == "答案"
    assert upd["citations"] == ["ck-1"]