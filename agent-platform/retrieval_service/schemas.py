"""独立检索服务契约（P1.2 契约冻结：input / output / error）。

透传给共享 `core.retrieval.RetrievedChunk` 结果，顶层只包一层 `{hits}`。

错误契约统一 `{"error": {"code": ..., "message": ..., "detail": ...}}`：
- 422  → 入参校验失败（缺 query / kb_id 非空非法）；
- 503  → 后端存储不可用（`StorageUnavailable`）；
- 500  → 其余上游失败（`UpstreamError`）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.retrieval import RetrievedChunk


class RetrievalRequest(BaseModel):
    """`POST /v1/retrieval` 入参。`kb_id` 必填（检索分区依据）；query 非空。"""

    query: str = Field(min_length=1, description="用户检索问题")
    kb_id: str = Field(min_length=1, description="知识库标识，参与标量过滤")
    topk: int = Field(default=10, ge=1, le=200, description="返回 TopN 条数")
    tenant_id: str | None = Field(default=None, description="租户标识，可选标量过滤")


class RetrievalHit(BaseModel):
    """单条检索结果（对齐 `core.retrieval.RetrievedChunk`）。"""

    chunk_id: str
    doc_id: str
    kb_id: str
    score: float
    source: Literal["vector", "keyword", "fused"]
    highlight: str | None = None
    text: str | None = Field(default=None, description="正文（服务层水合；可选）")


class RetrievalResponse(BaseModel):
    """`POST /v1/retrieval` 出参：TopN 命中列表。"""

    hits: list[RetrievalHit]


class EmbedRequest(BaseModel):
    """`POST /v1/embed` 入参（对齐 ingest/检索共用 embedding）。"""

    query: str = Field(min_length=1)


class EmbedResponse(BaseModel):
    """`POST /v1/embed` 出参：确定性向量 + 维度。"""

    vector: list[float]
    dim: int


# 内部编排调用也可复用的结构化结果（保持与 fetch 一致）
HitSource = Literal["vector", "keyword", "fused"]


__all__ = [
    "RetrievalRequest",
    "RetrievalHit",
    "RetrievalResponse",
    "EmbedRequest",
    "EmbedResponse",
    "HitSource",
    "RetrievedChunk",
]