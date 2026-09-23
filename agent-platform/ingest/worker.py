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

from typing import Any

from arq.connections import RedisSettings as ArqRedisSettings
from sqlalchemy import select, update

from core.config import get_settings
from core.exceptions import NotImplementedYet
from core.logging import get_logger
from core.storage import StorageContainer
from db.models import Document, DocumentStatus

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
    """重跑前清理两侧残留——可重入的前提（裁决 #3）。"""
    from db.mappings_es import delete_by_doc_id_body

    if container.es.connected:
        await container.es.client.delete_by_query(
            index=container.settings.es.index,
            **delete_by_doc_id_body(str(kb_id), str(doc_id)),
        )
    if container.milvus.connected:
        container.milvus.client.delete(
            collection_name=container.settings.milvus.collection,
            filter=f'doc_id == "{doc_id}"',
        )
    logger.info(
        "partial data cleaned before retry",
        extra={"extra_fields": {"doc_id": doc_id, "kb_id": kb_id}},
    )


async def load_document(container: StorageContainer, doc_id: int) -> Document | None:
    async with container.mysql.session() as session:
        return (
            await session.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# arq 任务
# ---------------------------------------------------------------------------


async def ingest_document(ctx: dict[str, Any], doc_id: int) -> None:
    """摄入主流程（增量 4 落地实际解析/切分/双写）。"""
    raise NotImplementedYet(_INCREMENT, f"文档 {doc_id} 的摄入链路尚未实现")


def _arq_redis_settings() -> ArqRedisSettings:
    """把统一配置里的 REDIS_URL 翻译成 arq 需要的连接参数。

    队列与 Checkpoint / 缓存共用同一 Redis 实例，靠 key 前缀隔离
    （队列键为 `queue:ingest:`，见 core.config.RedisSettings）。
    """
    settings = get_settings()
    return ArqRedisSettings.from_dsn(settings.redis.url)


class WorkerSettings:
    """arq worker 入口：`arq ingest.worker.WorkerSettings`"""

    functions = [ingest_document]
    redis_settings = _arq_redis_settings()
    max_tries = get_settings().ingest.max_tries
    job_timeout = get_settings().ingest.job_timeout
    # 单 worker 串行双写是裁决 #3 的前提，故不并发消费同一队列
    max_jobs = 1
