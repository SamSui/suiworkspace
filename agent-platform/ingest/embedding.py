"""Embedding 服务封装（增量 4.3 落地）。

设计线：与**检索侧共用同一 embedding 源**，保证「摄入落库的向量 = 检索 query 的向量」
（否则 4.6 端到端根本对不上，这是本增量的闭环前提）。

- `embed_texts(texts)`：批量向量化，维度对齐 `MILVUS_DIM`（`settings.milvus.dim`，
  默认 768），与 `db/schemas_milvus.build_collection_schema(dim=...)` 一致。
- `embed_query(text)`：单条（对齐 `langgraph_service.retrieval.embed_query` 的检索路径）。

**实现**：运行时 provider 默认 `echo`，复用 `langgraph_service.retrieval` 的确定性
归一化桩（同 query/短语 同向量，L2 归一）——因此**位级一致**、离线可复现。真实模型
（BGE / text-embedding）在 4/5 联动时按同一 provider 契约替换（届时核实模型资源与
授权，本增量不带外部 token/下载）。

失败可重试：`embed_texts` 支持 arq 退避重试的语义；单批内部按 `batch_size` 分批，
批内任一失败整体抛出 → worker 捕获置 FAILED，重跑由状态机回退（4.4 注错验证点）。
"""

from __future__ import annotations

from core.config import get_settings
from core.exceptions import UpstreamError
from core.logging import get_logger
from langgraph_service.retrieval import _stub_embed

logger = get_logger(__name__)

_DEFAULT_BATCH_SIZE = 32


def _dim() -> int:
    return get_settings().milvus.dim


def _provider() -> str:
    return get_settings().retrieval.embed_provider


async def embed_texts(
    texts: list[str], *, batch_size: int = _DEFAULT_BATCH_SIZE
) -> list[list[float]]:
    """批量向量化，维度 = MILVUS_DIM。空输入 → []。

    任一批失败抛 `UpstreamError`（可重试语义由 worker 的退避重跑承接）。
    """
    if not texts:
        return []
    dim = _dim()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        try:
            for t in batch:
                vectors.append(await _stub_embed(t, dim))
        except Exception as exc:  # noqa: BLE001 — 收敛为可重试错误码
            logger.warning(
                "embed batch failed",
                extra={"extra_fields": {"batch_start": start, "error": str(exc)}},
            )
            raise UpstreamError(f"embedding 失败: {exc}") from exc
    return vectors


async def embed_query(text: str) -> list[float]:
    """单条 query 向量化（4.6 端到端用，须与建库同一模型）。"""
    return await _stub_embed(text, _dim())


__all__ = ["embed_texts", "embed_query"]