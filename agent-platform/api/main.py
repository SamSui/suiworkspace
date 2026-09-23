"""FastAPI 网关应用工厂（进程组 ①）。

职责（设计文档 §1.2）：对外唯一入口，只做鉴权 / 限流 / 校验 / SSE 透传。
不直接访问 Milvus / ES / LLM —— 那些都在 `langgraph_service` 的节点里。

启动：
    uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api import __version__
from api.middlewares import (
    AuthMiddleware,
    RateLimitMiddleware,
    TraceMiddleware,
    register_exception_handlers,
)
from api.routers import agent, auth, chat, document, health, knowledge, task, users
from core.config import get_settings
from core.logging import get_logger, setup_logging
from core.storage import StorageContainer

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.app.log_level, json_output=settings.app.is_prod)

    container = StorageContainer(settings)
    # 网关以 fail_fast=False 启动：单个依赖不可用时服务仍能起来，
    # 真实可用性由 /healthz 暴露，避免一个存储抖动导致整个网关不可调度。
    await container.startup(bootstrap=True, fail_fast=False)
    app.state.container = container
    logger.info("api gateway started", extra={"extra_fields": {"env": settings.app.env}})

    try:
        yield
    finally:
        await container.shutdown()
        logger.info("api gateway stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="智能体中台 · API 网关",
        version=__version__,
        description="FastAPI 网关注入服务：鉴权 / 限流 / 校验 / SSE 透传",
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    # 添加顺序 = 由内向外（Starlette 后添加者在外层）。
    # 目标层级：Trace → Auth → RateLimit → routes
    # Auth 必须在 RateLimit 外层，限流才能按 user_id 而不是 IP 分组。
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(AuthMiddleware, settings=settings)
    app.add_middleware(TraceMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(chat.router)
    app.include_router(knowledge.router)
    app.include_router(document.router)
    app.include_router(agent.router)
    app.include_router(task.router)

    return app


app = create_app()
