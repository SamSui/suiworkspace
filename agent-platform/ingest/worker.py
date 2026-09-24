"""摄入 worker 与 `document.status` 状态机。

**双写一致性的实现落点**（架构裁决 #3）：
不引入分布式事务。单 worker 内按 chunk 串行双写（Milvus + ES），
MySQL `document.status` 是唯一真相源（0→1→2/3）协调推进：

    PENDING(0) --claim--> PROCESSING(1) --ok--> DONE(2)
                              |
                              +--异常--> FAILED(3) --退避重跑--> PROCESSING(1)

重跑前按 `doc_id` 清理两侧残留（Milvus + ES），保证可重入。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from arq.connections import RedisSettings as ArqRedisSettings
from sqlalchemy import select, update

from core.config import get_settings
from core.logging import get_logger
from core.storage import StorageContainer
from db.models import Document, DocumentStatus
from ingest import storage
from ingest.chunker import split_text
from ingest.embedding import embed_texts
from ingest.parser import ParserError, extract_text, validate_document

logger = get_logger(__name__)

_INCREMENT = "增量 4（ingest 摄入）"


# ---------------------------------------------------------------------------
# 状态机：这三个函数是真实实现，可被单测直接覆盖
# ---------------------------------------------------------------------------


async def mark_processing(container: StorageContainer, doc_id: int) -> bool:
    """认领任务：PENDING/FAILED → PROCESSING。

    用条件 UPDATE 保证并发下只有一个 worker 认领成功（乐观并发，无需显式锁）。
    """
    async with container.mysql.session() as session:
        result = await session.execute(
            update(Document)
            .where(
                Document.id == doc_id,
                Document.status.in_([DocumentStatus.PENDING, DocumentStatus.FAILED]),
            )
            .values(status=DocumentStatus.PROCESSING)
        )
        return result.rowcount == 1


async def mark_done(container: StorageContainer, doc_id: int, chunk_count: int) -> None:
    async with container.mysql.session() as session:
        await session.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(
                status=DocumentStatus.DONE,
                chunk_count=chunk_count,
                error_msg=None,
            )
        )


async def mark_failed(container: StorageContainer, doc_id: int, error: str) -> None:
    async with container.mysql.session() as session:
        await session.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(status=DocumentStatus.FAILED, error_msg=error[:1024])
        )


async def cleanup_partial(container: StorageContainer, doc_id: int, kb_id: int) -> None:
    """重跑前清理两侧残留——可重入的前提（裁决 #3）。

    收口增量 3 登记的待办：不再直接拿 `milvus.client.delete`（同步，阻塞事件循环），
    改走存储层 `delete_by_doc_id()`（async to_thread）。
    """
    from db.mappings_es import delete_by_doc_id_body

    if container.es.connected:
        await container.es.client.delete_by_query(
            index=container.settings.es.index,
            **delete_by_doc_id_body(str(kb_id), str(doc_id)),
        )
    if container.milvus.connected:
        await container.milvus.delete_by_doc_id(str(doc_id))
    logger.info(
        "partial data cleaned before retry",
        extra={"extra_fields": {"doc_id": doc_id, "kb_id": kb_id}},
    )


async def load_document(container: StorageContainer, doc_id: int) -> Document | None:
    async with container.mysql.session() as session:
        return (
            await session.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one_or_none()


def resolve_document_path(settings: Any, doc: Document) -> Path:
    """摄入源文件路径：`{data_dir}/{kb_id}/{doc_id}/{file_name}`（与 2.4 网关落盘约定一致）。"""
    return Path(settings.ingest.data_dir) / str(doc.kb_id) / str(doc.id) / doc.file_name


# ---------------------------------------------------------------------------
# arq 任务
# ---------------------------------------------------------------------------


async def ingest_document(ctx: dict[str, Any], doc_id: int) -> None:
    """摄入主流程：认领 → 解析 → 切分 → Embedding → 双写 → 状态推进。

    任一步异常：置 FAILED(3)，由 arq 退避重跑（`max_tries` 内）。重跑前清理双侧残留。
    arq ctx 需带 `container`（`WorkerSettings.ctx` / 启动钩子注入）；测试可直接传桩。
    """
    container = ctx.get("container") or ctx.get("shared")  # 兼容单测注入 / 真实 worker 注入
    if container is None:
        container = StorageContainer(get_settings())
        await container.startup(bootstrap=False, fail_fast=False)
    doc = await load_document(container, doc_id)
    if doc is None:
        logger.warning("document not found", extra={"extra_fields": {"doc_id": doc_id}})
        return

    prior_status = doc.status_enum
    if not await mark_processing(container, doc_id):
        logger.info(
            "doc already claimed by another worker, skip",
            extra={"extra_fields": {"doc_id": doc_id}},
        )
        return

    # 重跑（FAILED → PROCESSING）前清理双侧残留，保证可重入、不残留旧切片（裁决 #3）
    if prior_status == DocumentStatus.FAILED:
        await cleanup_partial(container, doc.id, doc.kb_id)

    try:
        path = resolve_document_path(container.settings, doc)
        if not path.exists():
            raise FileNotFoundError(f"源文件不存在: {path}")

        # 4.1 校验 + 解析
        validate_document(path)
        text = await asyncio.to_thread(extract_text, str(path))

        # 4.2 切分
        chunks = split_text(
            text,
            chunk_tokens=container.settings.ingest.chunk_tokens,
            overlap=container.settings.ingest.chunk_overlap,
        )

        # 4.3 Embedding（批量，维度=MILVUS_DIM）
        vectors = await embed_texts([c.text for c in chunks])

        # 4.4 串行双写（Milvus 先、ES 后，任一失败抛 → 状态机置 FAILED）
        chunk_count = await storage.write_chunks(
            container=container,
            kb_id=str(doc.kb_id),
            doc_id=str(doc.id),
            chunks=chunks,
            vectors=vectors,
            title=doc.file_name,
        )

        await mark_done(container, doc.id, chunk_count)
        logger.info(
            "document ingested",
            extra={"extra_fields": {"doc_id": doc.id, "chunks": chunk_count}},
        )
    except Exception as exc:  # noqa: BLE001 — 状态机兜底：一律置 FAILED，arq 退避重跑
        await mark_failed(container, doc.id, _describe_error(exc))
        logger.error(
            "document ingest failed",
            extra={"extra_fields": {"doc_id": doc.id, "error": str(exc)}},
        )
        raise


def _describe_error(exc: BaseException) -> str:
    if isinstance(exc, ParserError):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return str(exc)
    # 保留关键错误信息，截断到 1024（列宽）——Don't leak 敏感内容。
    return f"{type(exc).__name__}: {exc}"[:1024]


def _arq_redis_settings() -> ArqRedisSettings:
    """把统一配置里的 REDIS_URL 翻译成 arq 需要的连接参数。

    队列与 Checkpoint / 缓存共用同一 Redis 实例，靠 key 前缀隔离
    （队列键为 `queue:ingest:`，见 core.config.RedisSettings）。
    """
    settings = get_settings()
    return ArqRedisSettings.from_dsn(settings.redis.url)


async def _startup(ctx: dict[str, Any]) -> None:
    """arq worker 启动：建存储容器（真实连接，bootstrap 可后置）。"""
    container = StorageContainer(get_settings())
    await container.startup(bootstrap=True, fail_fast=False)
    ctx.setdefault("container", container)


async def _shutdown(ctx: dict[str, Any]) -> None:
    container = ctx.get("container")
    if container is not None:
        await container.shutdown()


class WorkerSettings:
    """arq worker 入口：`arq ingest.worker.WorkerSettings`"""

    functions = [ingest_document]
    redis_settings = _arq_redis_settings()
    max_tries = get_settings().ingest.max_tries
    job_timeout = get_settings().ingest.job_timeout
    # 单 worker 串行双写是裁决 #3 的前提，故不并发消费同一队列
    max_jobs = 1
    on_startup = _startup
    on_shutdown = _shutdown
