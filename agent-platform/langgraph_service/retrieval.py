"""检索侧兼容层（P1.2 把检索逻辑收敛到 `core.retrieval` 后的向后兼容壳）。

背景（SUIG-32）：混合检索 / embed / 缓存键 / 水合等纯逻辑已下沉到共享的
`core/retrieval.py`——独立检索服务（`retrieval_service`）与编排进程引用同一实现，
保证两路径 TopN 语义一致。本文件保留原公开名，让 `ingest/embedding`、`generate`
节点及既有测试的 `from langgraph_service.retrieval import ...` 导入继续可用，
不破坏既有 SSE / 编排契约。

新增独立服务的 `hybrid_search`（检索编排主线）也在 `core.retrieval`，编排节点
`nodes/retrieve` 直接引用之（见该模块）。
"""

from __future__ import annotations

from core.retrieval import (  # noqa: F401 — 仅重导出，保持公开名与行为不变
    EmbedFn,
    RerankFn,
    RetrievedChunk,
    _stub_embed,
    embed_query,
    fuse_dedup,
    hybrid_search,
    rerank_chunks,
    retrieval_cache_key,
    text_hydrate,
)

__all__ = [
    "embed_query",
    "rerank_chunks",
    "fuse_dedup",
    "text_hydrate",
    "retrieval_cache_key",
    "hybrid_search",
]