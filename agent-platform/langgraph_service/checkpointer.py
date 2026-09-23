"""Checkpointer 装配 —— **只接 RedisSaver**（架构裁决 #2）。

裁决原文：主选 RedisSaver；PostgresSaver 不做默认叠加，仅当出现
"审计 / 历史回放 / 确定性重放"硬需求时再引入（且须独立 PG，不蹭业务 MySQL）。

因此本模块不提供 PostgresSaver 分支——避免"顺手加一个"把裁决架空。
若未来确需引入，应作为独立决策走架构评审，而不是在这里加个 if。

为什么必须换掉默认实现：LangGraph 默认 `InMemorySaver` 存在单进程内存里，
多副本部署互不可见、重启即丢会话（设计文档 §6.1）。

⚠️ 待运行时校验：`AsyncRedisSaver` 的构造方式与 `asetup()` 调用需在装上
`langgraph-checkpoint-redis` 后按锁定版本核对一次（本增量环境无法安装依赖，
未做运行时验证）。见 issue 评论中的"未验证项"。
"""

from __future__ import annotations

from typing import Any

from core.config import RedisSettings
from core.exceptions import ConfigError
from core.logging import get_logger

logger = get_logger(__name__)

# checkpoint 键前缀，与 Redis 中其它用途隔离（设计文档 §2.6）
CHECKPOINT_PREFIX = "checkpoint:"


async def build_checkpointer(redis_settings: RedisSettings) -> Any:
    """构建并初始化 RedisSaver。

    用显式 redis client 构造（而非 `from_conn_string`）——后者的返回值形态
    在不同版本间是"客户端"还是"上下文管理器"有差异，显式构造更稳。

    `decode_responses=False`：Saver 需要读写二进制，不能开自动解码。
    """
    try:
        import redis.asyncio as aioredis
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    except ImportError as exc:  # pragma: no cover
        raise ConfigError(
            "缺少 langgraph-checkpoint-redis，请执行 "
            "`pip install langgraph-checkpoint-redis` 后重试"
        ) from exc

    client = aioredis.from_url(redis_settings.url, decode_responses=False)
    saver = AsyncRedisSaver(redis_client=client)
    # 首次使用需建索引/结构，幂等
    await saver.asetup()
    logger.info("checkpointer ready", extra={"extra_fields": {"backend": "redis"}})
    return saver


def thread_config(thread_id: str, *, checkpoint_ns: str = "") -> dict[str, Any]:
    """会话寻址配置。

    `thread_id` 即多实例共享状态的键——任一编排副本都能凭它恢复同一会话
    （与 `conversation.thread_id` 列一一对应）。
    """
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if checkpoint_ns:
        configurable["checkpoint_ns"] = checkpoint_ns
    return {"configurable": configurable}


__all__ = ["CHECKPOINT_PREFIX", "build_checkpointer", "thread_config"]
