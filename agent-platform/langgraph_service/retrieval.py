"""检索侧复用件：嵌入 / 重排 / 融合去重 / 缓存键（增量 3.4）。

真实 Embedding / Rerank 模型加载属增量 4/5 联动（业务与资源届时核实）——本文件
提供可插拔接口 + Echo 桩实现，运行时无真实模型也能端到端跑，单测以桩精确断言。

- `embed_query` → 向量（桩返回确定性伪向量，同 query 同向量，供缓存命中验证）。
- `rerank_chunks` → 按相关性重排取 top_k（桩形态：按 `score` 倒序，真实接 BGE-Reranker），
  并把耗时记入 `retrieval.rerank_ms` 埋点（验收：Rerank 耗时纳入埋点）。
- `fuse_dedup` → 多路召回融合（RRF）+ 按 `chunk_id` 去重，返回统一 `RetrievedChunk`（只带引用）。
- `text_hydrate(es, chunks)` → 按 chunk_id 从 ES 取回正文（文本不进 state，
  generate 组装 prompt 时用）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.logging import get_logger
from langgraph_service.graph.state import RetrievedChunk
from langgraph_service.metrics import record as record_metric

logger = get_logger(__name__)

_RRF_K = 60


async def _stub_embed(query: str, dim: int) -> list[float]:
    """确定性伪向量：以 query 哈希播种，保证同 query 同向量（供检索缓存命中验证）。"""
    import hashlib

    digest = hashlib.sha256(query.encode("utf-8")).digest()
    vec = [float((digest[i % len(digest)] / 255.0) * 2 - 1) for i in range(dim)]
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]


EmbedFn = Callable[[str, int], Any]


async def embed_query(query: str, dim: int, provider: str = "echo") -> list[float]:
    """按配置选嵌入；桩默认确定性，供检索缓存 key 复用。真实接入在 4/5 替换。"""
    return await _stub_embed(query, dim)  # noqa: A501 — 桩实现占位，接口稳定


# ---------- 融合去重 ----------

async def fuse_dedup(
    vector_hits: list[dict[str, Any]],
    keyword_hits: list[dict[str, Any]],
    top_k: int = 30,
) -> list[RetrievedChunk]:
    """两路召回 → RRF 融合 + 按 chunk_id 去重，返回统一 chunk 结构（只带引用）。

    每路 hits 需含 `chunk_id` 与 `score`；向量路还提供 `doc_id` / `kb_id`。
    """
    acc: dict[str, dict[str, Any]] = {}

    def _add(hit: dict[str, Any], source: str, rank: int) -> None:
        cid = str(hit.get("chunk_id") or hit.get("id"))
        if not cid:
            return
        entry = acc.setdefault(
            cid,
            {
                "chunk_id": cid,
                "doc_id": str(hit.get("doc_id") or ""),
                "kb_id": str(hit.get("kb_id") or ""),
                "sum": 0.0,
                "best": float(hit.get("score") or 0.0),
                "sources": set(),
                "highlight": hit.get("highlight"),
            },
        )
        entry["sum"] += 1.0 / (_RRF_K + rank)
        entry["sources"].add(source)
        if float(hit.get("score") or 0.0) > entry["best"]:
            entry["best"] = float(hit.get("score") or 0.0)

    for rank, hit in enumerate(vector_hits, start=1):
        _add(hit, "vector", rank)
    for rank, hit in enumerate(keyword_hits, start=1):
        _add(hit, "keyword", rank)

    ordered = sorted(acc.values(), key=lambda e: (e["sum"], e["best"]), reverse=True)
    out: list[RetrievedChunk] = []
    for e in ordered[:top_k]:
        srcs = sorted(e["sources"])
        out.append(
            {
                "chunk_id": e["chunk_id"],
                "doc_id": e["doc_id"],
                "kb_id": e["kb_id"],
                "score": e["best"],
                "source": srcs[0] if len(srcs) == 1 else "fused",
                "highlight": e["highlight"],
            }
        )
    return out


# ---------- 重排 ----------

RerankFn = Callable[[str, list[RetrievedChunk], int], Any]


async def rerank_chunks(
    query: str,
    chunks: list[RetrievedChunk],
    top_k: int,
    provider: str = "echo",
) -> list[RetrievedChunk]:
    """重排取 top_k，并把耗时记入 `retrieval.rerank_ms` 埋点（验收 3.4）。

    桩形态按现有 score 倒序；真实接 BGE-Reranker 时替换 `_rerank_impl`。
    """
    import time

    start = time.perf_counter()
    result = await _rerank_impl(provider, query, chunks, top_k)
    record_metric("retrieval.rerank_ms", (time.perf_counter() - start) * 1000)
    return result


async def _rerank_impl(
    provider: str, query: str, chunks: list[RetrievedChunk], top_k: int
) -> list[RetrievedChunk]:
    ordered = sorted(chunks, key=lambda c: c.get("score", 0.0), reverse=True)
    return ordered[:top_k]


# ---------- 正文水合（generate 用；文本不进 state）----------

async def text_hydrate(
    es: Any,
    chunks: list[RetrievedChunk],
) -> dict[str, tuple[str, str]]:
    """按 chunk_id 从 ES 取 (text, highlight)。取不到则回落 highlight / 空串。"""
    ids = [c["chunk_id"] for c in chunks]
    if not ids or es is None:
        return {}
    try:
        rows = await es.fetch_chunks(ids)
    except Exception as exc:  # noqa: BLE001 — 水合失败不阻断生成
        logger.warning("chunk hydrate failed", extra={"extra_fields": {"error": str(exc)}})
        rows = []
    by_id = {r["chunk_id"]: r for r in rows}
    out: dict[str, tuple[str, str]] = {}
    for c in chunks:
        r = by_id.get(c["chunk_id"])
        text = (r.get("text") if r else None) or c.get("highlight") or ""
        out[c["chunk_id"]] = (text, str(c.get("highlight") or ""))
    return out


# ---------- 检索缓存键 ----------

def retrieval_cache_key(query: str, kb_id: str | None) -> str:
    """缓存键：kb 隔离 + query 稳定哈希（桩向量确定性 → 同 query 同键，可命中）。"""
    import hashlib

    h = hashlib.sha256(query.encode("utf-8")).hexdigest()[:24]
    return f"{kb_id or '_global'}:{h}"


__all__ = [
    "embed_query",
    "rerank_chunks",
    "fuse_dedup",
    "text_hydrate",
    "retrieval_cache_key",
]