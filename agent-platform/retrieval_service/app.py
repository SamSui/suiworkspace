"""独立检索服务 FastAPI 应用工厂（进程组 ③，P1.2 落地）。

只对内网开放，供 `langgraph_service` 编排进程调用（裁决 #1：HTTP），
本身**不 import langgraph_service**，可独立水平扩展。

启动：
    uvicorn retrieval_service.app:app --host 0.0.0.0 --port 8101

对外契约：
- `POST /v1/retrieval` 传 `{query, kb_id, topk?, tenant_id?}` → `{hits:[...]}`；
- `POST /v1/embed`    传 `{query}` → `{vector, dim}`；
- `GET  /healthz`      存储健康 + 本服务可用性；
- `GET  /metrics`      Prometheus 文本（与编排/网关同一指标线）。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from core.config import get_settings
from core.exceptions import StorageUnavailable, UpstreamError
from core.logging import get_logger, setup_logging
from core.retrieval import embed_query, hybrid_search
from core.storage import StorageContainer
from observability import otel
from observability.prom import metrics as prom_metrics
from retrieval_service import __version__
from retrieval_service.schemas import (
    EmbedRequest,
    EmbedResponse,
    RetrievalHit,
    RetrievalRequest,
    RetrievalResponse,
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.app.log_level, json_output=settings.app.is_prod)

    container = StorageContainer(settings)
    # 独立服务以 fail_fast=False 启动：单点存储抖动不使服务不可调度，
    # 真实可用性由 /healthz 暴露（与网关/编排同口径）。
    await container.startup(bootstrap=False, fail_fast=False)
    app.state.container = container
    logger.info("retrieval service started", extra={"extra_fields": {"env": settings.app.env}})

    try:
        yield
    finally:
        await container.shutdown()
        logger.info("retrieval service stopped")


def _to_hits(hits: list[dict[str, Any]]) -> list[RetrievalHit]:
    """把共享 `hybrid_search` 返回的 RetrievedChunk 结构映射为契约出参。"""
    out: list[RetrievalHit] = []
    for h in hits:
        out.append(
            RetrievalHit(
                chunk_id=str(h.get("chunk_id", "")),
                doc_id=str(h.get("doc_id", "")),
                kb_id=str(h.get("kb_id", "")),
                score=float(h.get("score", 0.0)),
                source=h.get("source", "fused"),
                highlight=h.get("highlight"),
            )
        )
    return out


def create_app() -> FastAPI:
    app = FastAPI(
        title="智能体中台 · 独立检索服务",
        version=__version__,
        description="内网检索服务：ES BM25 + Milvus 向量混合检索 → TopN（可水平扩展）",
        lifespan=lifespan,
    )

    @app.exception_handler(StorageUnavailable)
    async def _storage_unavailable(_r: Request, exc: StorageUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "storage_unavailable", "message": exc.message}},
        )

    @app.exception_handler(UpstreamError)
    async def _upstream(_r: Request, exc: UpstreamError) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "upstream_error", "message": exc.message}},
        )

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        container: StorageContainer = request.app.state.container
        healthy, _results, summary = await container.health()
        return JSONResponse(status_code=200 if healthy else 503, content=summary)

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        body, ctype = prom_metrics.generate_latest()
        return Response(content=body, media_type=ctype)

    @app.post("/v1/retrieval", response_model=RetrievalResponse)
    async def retrieval(payload: RetrievalRequest, request: Request) -> RetrievalResponse:
        container: StorageContainer = request.app.state.container
        sett = get_settings()
        start = time.perf_counter()
        redis_store = getattr(container, "redis", None)
        milvus = getattr(container, "milvus", None)
        es = getattr(container, "es", None)
        r = sett.retrieval

        with otel.span("retrieval.v1", kb_id=payload.kb_id, query=payload.query[:80]) as rt_span:
            hits = await hybrid_search(
                es=es,
                milvus=milvus,
                redis_store=redis_store,
                cache_prefix=sett.redis.prefix_retrieval_cache,
                query=payload.query,
                kb_id=payload.kb_id,
                tenant_id=payload.tenant_id,
                vector_top_k=r.vector_top_k,
                keyword_top_k=r.keyword_top_k,
                rerank_top_k=payload.topk,
                embed_dim=r.embed_dim,
                embed_provider=r.embed_provider,
                rerank_provider=r.rerank_provider,
                cache_ttl_seconds=r.cache_ttl_seconds,
                vector_takes_tenant=True,
                keyword_takes_tenant=True,
            )
            rt_span.attributes["hits"] = str(len(hits))
            prom_metrics.observe_retrieval(time.perf_counter() - start)
            return RetrievalResponse(hits=_to_hits(hits))

    @app.post("/v1/embed", response_model=EmbedResponse)
    async def embed(payload: EmbedRequest) -> EmbedResponse:
        dim = get_settings().milvus.dim
        vector = await embed_query(payload.query, dim)
        return EmbedResponse(vector=vector, dim=dim)

    return app


app = create_app()