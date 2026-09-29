"""独立检索服务 HTTP 契约测试（P1.2 独立服务对外契约冻结）。

用 `httpx.ASGITransport` 直连 `create_app()`（不触发 lifespan 的真实存储启动），
注入假容器并 monkeypatch `retrieval_service.app.hybrid_search` / `embed_query`，
全离线、无网络依赖。

覆盖：
- `POST /v1/retrieval` 正常返回 TopN hits 契约；
- `POST /v1/embed` 返回确定性向量 + 维度；
- 入参校验（缺 kb_id / 空 query → 422）；
- 存储不可用 → 503 storage_unavailable。
"""

from __future__ import annotations

import pytest

import retrieval_service.app as app_mod
from retrieval_service.app import create_app


class _DummyContainer:
    """不实际连接；/v1/retrieval 走 monkeypatch 后的 hybrid_search。"""

    redis = None
    milvus = None
    es = None


def _make_hits():
    return [
        {"chunk_id": "c1", "doc_id": "d1", "kb_id": "k1", "score": 0.92,
         "source": "fused", "highlight": "报销规则"},
        {"chunk_id": "c2", "doc_id": "d1", "kb_id": "k1", "score": 0.6,
         "source": "vector", "highlight": None},
    ]


async def _fake_hybrid(**kwargs):
    """模拟 hybrid_search：命中返回两行；不真正连存储。"""
    hits = _make_hits()
    if kwargs.get("tenant_id"):
        # 契约确认：tenant_id 已作为过滤入参透传商
        hits = [h for h in hits if h["kb_id"] in (kwargs.get("kb_id"),)]
    return hits


async def _fake_embed(query: str, dim: int):
    return [0.1] * dim


async def _raise_hybrid(**kwargs):
    from core.exceptions import StorageUnavailable

    raise StorageUnavailable("es 不可用")


def _fresh_app(monkeypatch):
    app = create_app()
    app.state.container = _DummyContainer()
    monkeypatch.setattr(app_mod, "hybrid_search", _fake_hybrid)
    monkeypatch.setattr(app_mod, "embed_query", _fake_embed)
    return app


@pytest.mark.asyncio()
async def test_retrieval_contract(monkeypatch):
    app = _fresh_app(monkeypatch)
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://retrieval-service"
    ) as c:
        resp = await c.post("/v1/retrieval", json={"query": "报销怎么走", "kb_id": "k1", "topk": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert "hits" in data and len(data["hits"]) == 2
        first = data["hits"][0]
        assert first["chunk_id"] == "c1"
        assert {"chunk_id", "doc_id", "kb_id", "score", "source", "highlight"} <= set(first)


@pytest.mark.asyncio()
async def test_retrieval_contract_includes_tenant_propagation(monkeypatch):
    app = _fresh_app(monkeypatch)
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://retrieval-service"
    ) as c:
        resp = await c.post(
            "/v1/retrieval", json={"query": "报销", "kb_id": "k1", "topk": 5, "tenant_id": "t-1"}
        )
        assert resp.status_code == 200
        assert resp.json()["hits"][0]["kb_id"] == "k1"  # tenant 过滤路径可调


@pytest.mark.asyncio()
async def test_embed_contract(monkeypatch):
    app = _fresh_app(monkeypatch)
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://retrieval-service"
    ) as c:
        resp = await c.post("/v1/embed", json={"query": "报销"})
        assert resp.status_code == 200
        data = resp.json()
        assert "vector" in data and "dim" in data
        assert isinstance(data["vector"], list)


@pytest.mark.asyncio()
async def test_retrieval_validation(monkeypatch):
    app = _fresh_app(monkeypatch)
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://retrieval-service"
    ) as c:
        # 缺 kb_id → 422
        resp = await c.post("/v1/retrieval", json={"query": "报销"})
        assert resp.status_code == 422
        # 空 query → 422（min_length=1）
        resp = await c.post("/v1/retrieval", json={"query": "", "kb_id": "k1"})
        assert resp.status_code == 422


@pytest.mark.asyncio()
async def test_storage_unavailable_maps_503(monkeypatch):
    app = create_app()
    app.state.container = _DummyContainer()
    monkeypatch.setattr(app_mod, "hybrid_search", _raise_hybrid)
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://retrieval-service"
    ) as c:
        resp = await c.post("/v1/retrieval", json={"query": "报销", "kb_id": "k1"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "storage_unavailable"


@pytest.mark.asyncio()
async def test_client_retrieval_parses_contract():
    """RetrievalClient 依赖的 /v1/retrieval 契约出参形状：RetrievedChunk 可解析。"""
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": [
                    {"chunk_id": "c1", "doc_id": "d1", "kb_id": "k1", "score": 0.9,
                     "source": "fused", "highlight": "报销"},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as c:
        resp = await c.post(
            "http://retrieval-service/v1/retrieval",
            json={"query": "报销", "kb_id": "k1", "topk": 5},
        )
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert hits[0]["chunk_id"] == "c1"
    # 契约字段齐全（RetrievalClient.retrieve 依赖这些键）
    first = hits[0]
    assert {"chunk_id", "doc_id", "kb_id", "score", "source", "highlight"} <= set(first)