"""LangGraph 编排服务入口（进程组 ②）。

只对内网开放，由 `api` 网关调用（裁决 #1：HTTP+SSE）。
Checkpoint 只接 RedisSaver（裁决 #2）。

启动：
    uvicorn langgraph_service.main:app --host 0.0.0.0 --port 8100
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.config import get_settings
from core.exceptions import AppError, NotImplementedYet
from core.logging import get_logger, setup_logging
from core.storage import StorageContainer
from langgraph_service import __version__
from langgraph_service.checkpointer import build_checkpointer

logger = get_logger(__name__)

_INCREMENT = "增量 3（langgraph 编排）"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.app.log_level, json_output=settings.app.is_prod)

    container = StorageContainer(settings)
    await container.startup(bootstrap=False, fail_fast=False)
    app.state.container = container

    # Checkpoint 后端：RedisSaver 是唯一选择
    try:
        app.state.checkpointer = await build_checkpointer(settings.redis)
    except AppError as exc:
        logger.warning(
            "checkpointer 初始化失败，编排能力不可用",
            extra={"extra_fields": {"error": exc.message}},
        )
        app.state.checkpointer = None

    logger.info("langgraph service started")
    try:
        yield
    finally:
        await container.shutdown()
        logger.info("langgraph service stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="智能体中台 · LangGraph 编排服务",
        version=__version__,
        description="内网编排服务：Agent 图执行 / 多实例共享状态 / HITL",
        lifespan=lifespan,
    )

    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
        )

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        """探活：存储 + checkpoint 后端。编排不可用即视为不健康。"""
        container: StorageContainer = request.app.state.container
        healthy, _results, summary = await container.health()
        has_checkpointer = request.app.state.checkpointer is not None
        summary["checkpointer"] = "redis" if has_checkpointer else "unavailable"
        healthy = healthy and has_checkpointer
        return JSONResponse(status_code=200 if healthy else 503, content=summary)

    class RunRequest(BaseModel):
        thread_id: str = Field(description="会话寻址键，与 conversation.thread_id 对应")
        query: str = Field(min_length=1)
        user_id: int
        kb_id: str | None = None

    class ResumeRequest(BaseModel):
        thread_id: str
        value: str

    @app.post("/v1/stream")
    async def stream(_payload: RunRequest) -> None:
        """SSE 流式执行图。网关侧只做透传，不缓冲。"""
        raise NotImplementedYet(_INCREMENT)

    @app.post("/v1/resume")
    async def resume(_payload: ResumeRequest) -> None:
        """HITL 恢复：从 RedisSaver checkpoint 续跑挂起的图。"""
        raise NotImplementedYet(_INCREMENT)

    @app.get("/v1/threads/{thread_id}")
    async def get_thread(_thread_id: str) -> None:
        """读取会话当前状态 / checkpoint。"""
        raise NotImplementedYet(_INCREMENT)

    return app


app = create_app()
