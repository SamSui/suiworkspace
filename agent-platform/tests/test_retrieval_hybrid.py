"""core.retrieval.hybrid_search 单测（P1.2 · 独立检索服务业务主线）。

覆盖：
- 缓存命中直接返回（跳过召回/重排）；
- 双路召回 → RRF 融合 + 去重 → 重排取 top_k；
- `tenant_id` 非空时并入向量路 / 关键词路标量过滤（隔离生效）；
- 无 tenant_id 时不带过滤（兼容既有路径）。
"""

from __future__ import annotations

from core.retrieval import _stub_embed, hybrid_search, retrieval_cache_key


class _FakeRedis:
    def __init__(self, cache=None):
        self.cache = cache or {}

    async def get_json(self, key):
        return self.cache.get(key)

    async def set_json(self, key, value, *, ttl_seconds):
        self.cache[key] = list(value)


class _FakeMilvus:
    """记录调用参数，可配置返回行。"""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[dict] = []

    async def search(self, vec, *, kb_id, top_k, output_fields=None, tenant_id=None):
        self.calls.append({"kb_id": kb_id, "tenant_id": tenant_id})
        return self.rows


class _FakeES:
    def __init__(self, hits):
        self.hits = hits
        self.calls: list[dict] = []

    async def keyword_search(self, query, *, kb_id, top_k, highlight=True, tenant_id=None):
        self.calls.append({"kb_id": kb_id, "tenant_id": tenant_id})
        return self.hits


def _key(kb, query):
    return "cache:ret:" + retrieval_cache_key(query, kb)


async def test_hybrid_cache_hit_skips_recall():
    redis = _FakeRedis({_key("k1", "报销"): [{"chunk_id": "pre", "score": 1.0}]})
    milvus = _FakeMilvus([])
    es = _FakeES([])
    hits = await hybrid_search(
        es=es, milvus=milvus, redis_store=redis, cache_prefix="cache:ret:",
        query="报销", kb_id="k1", rerank_top_k=5,
    )
    assert hits[0]["chunk_id"] == "pre"
    assert milvus.calls == [] and es.calls == []  # 缓存命中未触达存储


async def test_fuse_dedup_rerank_topk():
    redis = _FakeRedis({})
    milvus = _FakeMilvus([
        {"chunk_id": "c1", "score": 0.9},
        {"chunk_id": "c2", "score": 0.7},
    ])
    es = _FakeES([
        {"chunk_id": "c1", "score": 8.0, "highlight": "报销"},
        {"chunk_id": "c3", "score": 6.0, "highlight": "流程"},
    ])
    hits = await hybrid_search(
        es=es, milvus=milvus, redis_store=redis, cache_prefix="cache:ret:",
        query="报销", kb_id="k1", rerank_top_k=2,
    )
    ids = [h["chunk_id"] for h in hits]
    assert ids == ["c1", "c3"]  # c1 双路融合排最前，top_k=2 截断
    by = {h["chunk_id"]: h for h in hits}
    assert by["c1"]["source"] == "fused"


async def test_tenant_filter_threaded_to_both_recalls():
    redis = _FakeRedis({})
    milvus = _FakeMilvus([{"chunk_id": "c1", "score": 0.9}])
    es = _FakeES([{"chunk_id": "c3", "score": 6.0, "highlight": "x"}])
    await hybrid_search(
        es=es, milvus=milvus, redis_store=redis, cache_prefix="cache:ret:",
        query="报销", kb_id="k1", tenant_id="t-42",
        vector_takes_tenant=True, keyword_takes_tenant=True,
    )
    assert milvus.calls and milvus.calls[0]["tenant_id"] == "t-42"
    assert es.calls and es.calls[0]["tenant_id"] == "t-42"


async def test_no_tenant_means_no_filter():
    redis = _FakeRedis({})
    milvus = _FakeMilvus([{"chunk_id": "c1", "score": 0.9}])
    es = _FakeES([{"chunk_id": "c3", "score": 6.0, "highlight": "x"}])
    await hybrid_search(
        es=es, milvus=milvus, redis_store=redis, cache_prefix="cache:ret:",
        query="报销", kb_id="k1",
        vector_takes_tenant=True, keyword_takes_tenant=True,
    )
    assert milvus.calls[0]["tenant_id"] is None
    assert es.calls[0]["tenant_id"] is None


async def test_stub_embed_deterministic():
    a = await _stub_embed("报销", 8)
    b = await _stub_embed("报销", 8)
    assert a == b  # 同 query 同向量 → 缓存命中可依赖确定性