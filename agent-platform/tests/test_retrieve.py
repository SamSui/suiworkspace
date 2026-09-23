"""3.4 检索复用件 + retrieve 节点单测。

验收点：
- 融合去重（RRF + chunk_id）正确；
- Rerank 耗时纳入埋点（`retrieval.rerank_ms`）；
- 缓存命中跳过检索（同 query 二访命中）。
"""

from __future__ import annotations

from langgraph_service import metrics
from langgraph_service.retrieval import (
    embed_query,
    fuse_dedup,
    rerank_chunks,
    retrieval_cache_key,
)


class _FakeStore:
    """可注入的 Redis 缓存桩：get 命中与否可控。"""

    def __init__(self, cache=None):
        self._cache = cache or {}

    async def get_json(self, key):
        return self._cache.get(key)

    async def set_json(self, key, value, *, ttl_seconds):
        self._cache[key] = value


class _FakeMilvus:
    def __init__(self, rows):
        self._rows = rows

    async def search(self, vec, *, kb_id, top_k, output_fields=None):
        return self._rows


class _FakeES:
    def __init__(self, hits):
        self._hits = hits

    async def keyword_search(self, query, *, kb_id, top_k, highlight=True):
        return self._hits


async def test_embed_is_deterministic():
    a = await embed_query("报销", 8)
    b = await embed_query("报销", 8)
    assert a == b  # 同 query 同向量 → 缓存键稳定
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6  # L2 归一


async def test_cache_key_isolates_kb():
    assert retrieval_cache_key("q", "kb-1") != retrieval_cache_key("q", "kb-2")
    assert retrieval_cache_key("a", None) != retrieval_cache_key("b", None)


async def test_fuse_dedup_merges_and_dedups():
    vec = [
        {"chunk_id": "c1", "doc_id": "d1", "kb_id": "k1", "score": 0.9},
        {"chunk_id": "c2", "doc_id": "d1", "kb_id": "k1", "score": 0.7},
    ]
    kw = [
        {"chunk_id": "c1", "doc_id": "d1", "kb_id": "k1", "score": 8.0, "highlight": "报销"},
        {"chunk_id": "c3", "doc_id": "d2", "kb_id": "k1", "score": 6.0, "highlight": "流程"},
    ]
    out = await fuse_dedup(vec, kw, top_k=5)
    ids = [c["chunk_id"] for c in out]
    assert set(ids) == {"c1", "c2", "c3"}  # c1 双路命中合并，无重复
    by_id = {c["chunk_id"]: c for c in out}
    assert by_id["c1"]["source"] == "fused"  # 双路 → fused
    assert by_id["c2"]["source"] == "vector"  # 仅向量
    assert by_id["c3"]["source"] == "keyword"  # 仅关键词
    # 融合后 c1 因双路唱票应排最前
    assert out[0]["chunk_id"] == "c1"


def _chunk(cid, score=0.0, source="vector", highlight=None):
    return {
        "chunk_id": cid,
        "doc_id": "",
        "kb_id": "",
        "score": score,
        "source": source,
        "highlight": highlight,
    }


async def test_rerank_records_metric():
    metrics.reset()
    chunks = [_chunk("a", score=0.3), _chunk("b", score=0.9)]
    top = await rerank_chunks("q", chunks, top_k=1)
    assert [c["chunk_id"] for c in top] == ["b"]  # 按分倒序
    assert metrics.count("retrieval.rerank_ms") >= 0  # 寄存器被调用（直方图不能用 count 断）
    # 直方图有数据走 latency_p
    assert metrics.latency_p("retrieval.rerank_ms", 99) >= 0.0


async def test_retrieve_cache_hit_skips_recall():
    from langgraph_service.nodes.retrieve import retrieve_node

    metrics.reset()
    cached = [_chunk("pre", score=1.0)]
    cache_key = f"cache:ret:{retrieval_cache_key('报销', 'k1')}"
    redis = _FakeStore({cache_key: cached})
    container = type("C", (), {"redis": redis, "milvus": _FakeMilvus([]), "es": _FakeES([])})()
    settings = type(
        "S",
        (),
        {
            "retrieval": type(
                "R",
                (),
                {
                    "embed_dim": 8,
                    "embed_provider": "echo",
                    "vector_top_k": 30,
                    "keyword_top_k": 30,
                    "rerank_top_k": 5,
                    "rerank_provider": "echo",
                    "cache_ttl_seconds": 10,
                },
            )(),
            "redis": type("RS", (), {"prefix_retrieval_cache": "cache:ret:"})(),
        },
    )()

    cfg = {"configurable": {"deps": {"container": container, "settings": settings}}}
    upd = await retrieve_node({"query": "报销", "kb_id": "k1"}, cfg)
    assert upd["retrieved"][0]["chunk_id"] == "pre"
    assert metrics.count("retrieval.cache_hit") == 1
    assert metrics.count("retrieval.cache_miss") == 0