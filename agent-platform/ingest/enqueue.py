"""上传入队：网关把 `document(status=0)` 投到 arq 摄入队列（增量 2.4）。

网关请求路径里只做「入队」，绝不解析/摄入——解析在 `ingest/worker.py` 的
`WorkerSettings.functions[ingest_document]` 消费（增量 4 落地实际链路）。

用 arq 的 `create_pool` + `enqueue_job`，与 `ingest.worker.WorkerSettings`
共用同一 Redis（key 由 arq 按 DSN 统一管理，与 Checkpoint/缓存靠前缀隔离）。
"""

from __future__ import annotations

from arq import create_pool
from arq.connections import RedisSettings as ArqRedisSettings

from core.logging import get_logger
from core.storage import StorageContainer

logger = get_logger(__name__)

# 与 ingest.worker.WorkerSettings.functions 里的任务名保持一致
_INGEST_FN = "ingest_document"


async def enqueue_document(container: StorageContainer, doc_id: int) -> bool:
    """把 `doc_id` 投递到摄入队列，成功返回 True。

    返回布尔而非抛错：入队失败由调用方决定是透传 502 还是容忍，
    本函数保持「尽力而为 + 记日志」。
    """
    arq_settings = ArqRedisSettings.from_dsn(container.settings.redis.url)
    pool = await create_pool(arq_settings)
    try:
        job = await pool.enqueue_job(_INGEST_FN, doc_id)
        logger.info(
            "document enqueued",
            extra={"extra_fields": {"doc_id": doc_id, "enqueued": job is not None}},
        )
        return job is not None
    except Exception as exc:  # noqa: BLE001 — 队列不可用不应让业务 500
        logger.warning(
            "document enqueue failed", extra={"extra_fields": {"doc_id": doc_id, "error": str(exc)}}
        )
        return False
    finally:
        await pool.aclose()