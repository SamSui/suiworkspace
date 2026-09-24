"""双写落库（增量 4.4 落地）。

单 worker 内按 chunk 串行写 Milvus + ES（裁决 #3，不做分布式事务）：
- **Milvus**：只写向量 + chunk_id/kb_id/doc_id/content_len（原文在 ES）；
- **ES**：写原文切片，`_id` 固定为 chunk_id → 重跑天然幂等覆盖（不需先删）。

任一侧失败抛异常 → worker 状态机置 FAILED(3)，退避重跑前先 `cleanup` 两侧残留（可重入）。

`chunk_id` 规约：`{doc_id}:{chunk_index:06d}`（与 ES `_id` / Milvus 主键共用，
供检索 hit 回链）。kb_id/doc_id 统一以字符串落库（Milvus/ES 侧 VARCHAR / keyword）。
"""

from __future__ import annotations

from typing import Any

from core.exceptions import UpstreamError
from core.logging import get_logger
from core.storage import StorageContainer

logger = get_logger(__name__)


def build_chunk_id(doc_id: str, index: int) -> str:
    """切片全局唯一 ID：与 ES `_id` / Milvus 主键一致。"""
    return f"{doc_id}:{index:06d}"


async def write_chunks(
    *,
    container: StorageContainer,
    kb_id: str,
    doc_id: str,
    chunks: list[Any],
    vectors: list[list[float]],
    title: str | None = None,
) -> int:
    """串行双写两侧，返回写入切片数。任一侧失败抛异常 → 状态机置 FAILED 并退避重跑。

    `container` 由 worker 注入（arq ctx 携带）；`chunks` 为 `ingest.chunker.Chunk`，
    `vectors` 与之等长对齐。
    """
    if len(chunks) != len(vectors):
        raise ValueError(f"chunks/vectors 数量不一致: {len(chunks)} != {len(vectors)}")
    if not chunks:
        return 0

    chunk_ids = [build_chunk_id(doc_id, c.index) for c in chunks]

    # 1) Milvus 侧：向量 + 回链主键
    milvus_rows: list[dict[str, Any]] = []
    for cid, chunk, vec in zip(chunk_ids, chunks, vectors, strict=True):
        milvus_rows.append(
            {
                "chunk_id": cid,
                "kb_id": kb_id,
                "doc_id": doc_id,
                "content_len": chunk.token_count,
                "embedding": vec,
            }
        )
    try:
        await container.milvus.insert(milvus_rows)
    except Exception as exc:  # noqa: BLE001 — 统一收敛为可重试的 UpstreamError
        raise UpstreamError(f"Milvus 写入失败: {exc}") from exc

    # 2) ES 侧：原文切片（`_id`=chunk_id，幂等覆盖；`chunk_id` 不再写入 `_source`，
    #    因 mapping 为 strict 且 chunk_id 即 `_id`，重复字段会被拒绝）
    es_documents: list[dict[str, Any]] = []
    for chunk in chunks:
        es_documents.append(
            {
                "kb_id": kb_id,
                "doc_id": doc_id,
                "title": title,
                "chunk_index": chunk.index,
                "content_len": chunk.token_count,
                "text": chunk.text,
            }
        )
    try:
        await container.es.bulk_index(es_documents)
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError(f"ES 写入失败: {exc}") from exc

    logger.info("dual-write ok", extra={"extra_fields": {"doc_id": doc_id, "count": len(chunks)}})
    return len(chunks)


async def cleanup(container: StorageContainer, *, kb_id: str, doc_id: str) -> None:
    """重跑前清理两侧残留——可重入前提（裁决 #3）。

    与 `ingest.worker.cleanup_partial` 等价但收口到本存储模块；worker 统一走
    `container.milvus.delete_by_doc_id` / `container.es.delete_by_doc_id`（async to_thread）。
    """
    # ES 先删（原文量小、幂等）；Milvus 再删向量
    es_deleted = 0
    if container.es.connected:
        try:
            es_deleted = await container.es.delete_by_doc_id(str(kb_id), str(doc_id))
        except Exception as exc:  # noqa: BLE001 — 清理失败不阻断重跑，查询侧有兜底
            logger.warning(
                "es cleanup failed",
                extra={"extra_fields": {"doc_id": doc_id, "error": str(exc)}},
            )
    milvus_deleted = 0
    if container.milvus.connected:
        try:
            milvus_deleted = await container.milvus.delete_by_doc_id(str(doc_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "milvus cleanup failed",
                extra={"extra_fields": {"doc_id": doc_id, "error": str(exc)}},
            )
    logger.info(
        "cleanup done",
        extra={
            "extra_fields": {
                "doc_id": doc_id,
                "es_deleted": es_deleted,
                "milvus_deleted": milvus_deleted,
            }
        },
    )


__all__ = ["write_chunks", "build_chunk_id", "cleanup"]