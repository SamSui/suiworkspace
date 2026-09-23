"""LangGraph 编排服务入口（进程组 ②，增量 3 落地）。

只对内网开放，由 `api` 网关调用（裁决 #1：HTTP+SSE）。
Checkpoint 只接 RedisSaver（裁决 #2）。

启动：
    uvicorn langgraph_service.main:app --host 0.0.0.0 --port 8100

对外契约（对齐已冻结 SSE，见 `sse.py`）：
- `POST /v1/stream` 传 `{thread_id, query, user_id, kb_id, hitl_required}`，SSE 流式返回
  token / interrupt / done / error 事件（seq 单调）。
- `POST /v1/resume`  传 `{thread_id, value, user_id}`，从 RedisSaver checkpoint 续跑挂起的图。
- `GET  /v1/threads/{thread_id}` 读会话当前状态 / checkpoint。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from core.config import get_settings
from core.exceptions import AppError, NotFound, NotImplementedYet, UpstreamError
from core.logging import get_logger, setup_logging
from core.storage import StorageContainer
from langgraph_service import __version__
from langgraph_service.checkpointer import build_checkpointer, thread_config
from langgraph_service.graph.rag_graph import build_rag_graph
from langgraph_service.llm import LLMClient
from langgraph_service.metrics import bump
from langgraph_service.sse import HEARTBEAT_INTERVAL, SSEEncoder

logger = get_logger(__name__)

_INCREMENT = "增量 3（langgraph 编排）"


class RunRequest(BaseModel):
    """`POST /v1/stream` 入参。模块级：FastAPI 需能解析为 body schema。"""

    thread_id: str = Field(description="会话寻址键，与 conversation.thread_id 对应")
    query: str = Field(min_length=1)
    user_id: int
    kb_id: int | None = None
    hitl_required: bool = Field(
        default=False, description="命中需人工审批场景则图在 generate 前挂起"
    )


class ResumeRequest(BaseModel):
    """`POST /v1/resume` 入参。"""

    thread_id: str
    value: str
    user_id: int | None = None

# 编排侧不回写 MySQL；`message_id` 在线程内自增，便于网关/收录对齐 message 行。
# Redis 可用时用 INCR（跨实例一致），否则退本地计数器。
_local_seq = 0
_local_seq_lock = Lock()


async def _next_message_id(container: StorageContainer | None, thread_id: str) -> int:
    if container is not None:
        redis = container.redis
        if redis is not None:
            try:
                return int(await redis.client.incr(f"msgseq:{thread_id}"))
            except Exception:  # noqa: BLE001 — Redis 不可用时落本地计数器
                pass
    global _local_seq  # noqa: PLW0603
    with _local_seq_lock:
        _local_seq += 1
        return _local_seq


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.app.log_level, json_output=settings.app.is_prod)

    container = StorageContainer(settings)
    await container.startup(bootstrap=False, fail_fast=False)
    app.state.container = container

    # Checkpoint 后端：RedisSaver 是唯一选择
    checkpointer = None
    try:
        checkpointer = await build_checkpointer(settings.redis)
    except AppError as exc:
        logger.warning(
            "checkpointer 初始化失败，编排能力不可用",
            extra={"extra_fields": {"error": exc.message}},
        )
    app.state.checkpointer = checkpointer

    # LLM client（多 provider 兜底；无凭据退化为 Echo 桩）
    try:
        llm_client = await LLMClient.from_settings(settings.llm)
    except Exception as exc:  # noqa: BLE001 — 装配失败降级不阻断启动
        logger.warning("llm client 装配失败", extra={"extra_fields": {"error": str(exc)}})
        llm_client = None
    app.state.llm_client = llm_client

    # 图编译
    app.state.graph = build_rag_graph(checkpointer)

    logger.info("langgraph service started")
    try:
        yield
    finally:
        if llm_client is not None:
            await llm_client.aclose()
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

    @app.post("/v1/stream")
    async def stream(payload: RunRequest, request: Request) -> StreamingResponse:
        """SSE 流式执行图。网关侧只做透传，不缓冲。"""
        graph = request.app.state.graph
        if graph is None:
            raise NotImplementedYet(_INCREMENT, "图未就绪（checkpointer/依赖缺失）")
        llm_client = request.app.state.llm_client
        container: StorageContainer = request.app.state.container
        settings = get_settings()

        queue: asyncio.Queue[str] = asyncio.Queue()
        box: dict[str, Any] = {}
        enc = SSEEncoder()  # 单一编码器：token 与 interrupt/done/error 共享同一 seq 序列
        cfg = thread_config(payload.thread_id)
        cfg["configurable"]["deps"] = {
            "container": container,
            "settings": settings,
            "llm_client": llm_client,
            # 节点每吐一个 token 调一次；这里即时编码为 SSE 帧入队（与后续 done 同序列）
            "emit": lambda p: queue.put_nowait(enc.token(p["text"])),
        }
        state: dict[str, Any] = {
            "query": payload.query,
            "user_id": payload.user_id,
            "kb_id": payload.kb_id,
            "thread_id": payload.thread_id,
            "hitl_required": payload.hitl_required,
        }
        run_task = asyncio.create_task(_run_graph(graph, state, cfg, box, queue))

        async def _gen() -> AsyncIterator[str]:
            try:
                while True:
                    try:
                        frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
                    except asyncio.TimeoutError:
                        # 无帧可发（图执行中空闲）→ 心跳保活，应对 LB 空闲掐断
                        yield enc.heartbeat()
                        continue
                    if frame is None:
                        break  # 哨兵：图已结束（见 _run_graph finally）
                    yield frame
                # 图结束：error / interrupt / done
                if box.get("error"):
                    exc = box["error"]
                    yield enc.error(
                        code="llm_timeout" if isinstance(exc, UpstreamError) else "stream_error",
                        message=str(exc),
                        trace_id=box.get("trace_id", ""),
                    )
                elif box.get("interrupt"):
                    yield enc.interrupt()
                else:
                    final = box.get("final") or {}
                    usage = final.get("usage") or {}
                    mid = await _next_message_id(container, payload.thread_id)
                    yield enc.done(
                        message_id=mid, usage={str(k): int(v) for k, v in usage.items()}
                    )
                bump("stream.done")
            except asyncio.CancelledError:  # 客户端断开
                run_task.cancel()
                raise
            except Exception as exc:  # noqa: BLE001 — 兜底错误事件
                try:
                    yield enc.error(code="stream_error", message=str(exc), trace_id="")
                except Exception:  # noqa: BLE001 — 保证生成器不二次抛
                    pass

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @app.post("/v1/resume")
    async def resume(payload: ResumeRequest, request: Request) -> StreamingResponse:
        """HITL 恢复：从 RedisSaver checkpoint 续跑挂起的图。"""
        graph = request.app.state.graph
        if graph is None:
            raise NotImplementedYet(_INCREMENT, "图未就绪")
        container: StorageContainer = request.app.state.container
        settings = get_settings()
        queue: asyncio.Queue[str] = asyncio.Queue()
        box: dict[str, Any] = {}
        enc = SSEEncoder()  # 与 token 共享同一 seq 序列
        cfg = thread_config(payload.thread_id)
        cfg["configurable"]["deps"] = {
            "container": container,
            "settings": settings,
            "llm_client": request.app.state.llm_client,
            "emit": lambda p: queue.put_nowait(enc.token(p["text"])),
        }
        run_task = asyncio.create_task(_resume_graph(graph, cfg, payload.value, box, queue))

        async def _gen() -> AsyncIterator[str]:
            try:
                while True:
                    try:
                        frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
                    except asyncio.TimeoutError:
                        yield enc.heartbeat()
                        continue
                    if frame is None:
                        break  # 哨兵：图已结束
                    yield frame
                if box.get("error"):
                    yield enc.error(code="stream_error", message=str(box["error"]), trace_id="")
                elif box.get("interrupt"):
                    yield enc.interrupt()
                else:
                    usage = (box.get("final") or {}).get("usage") or {}
                    mid = await _next_message_id(container, payload.thread_id)
                    yield enc.done(
                        message_id=mid, usage={str(k): int(v) for k, v in usage.items()}
                    )
            except asyncio.CancelledError:  # 客户端断开
                run_task.cancel()
                raise
            except Exception as exc:  # noqa: BLE001
                try:
                    yield enc.error(code="stream_error", message=str(exc), trace_id="")
                except Exception:  # noqa: BLE001
                    pass

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @app.get("/v1/threads/{thread_id}")
    async def get_thread(thread_id: str, request: Request) -> JSONResponse:
        """读取会话当前状态 / checkpoint。"""
        graph = request.app.state.graph
        if graph is None:
            raise NotImplementedYet(_INCREMENT, "图未就绪")
        cfg = thread_config(thread_id)
        snapshot = await _read_thread(graph, cfg)
        if snapshot is None:
            raise NotFound(f"会话不存在或尚无 checkpoint: {thread_id}")
        return JSONResponse(content=snapshot)

    return app


async def _run_graph(
    graph: Any,
    state: dict[str, Any],
    cfg: dict[str, Any],
    box: dict[str, Any],
    queue: asyncio.Queue[str],
) -> None:
    """后台执行图：收集 interrupt / final / error，并投哨兵通知 _gen 结束。

    哨兵 `None` 让 _gen 立即退出而非阻塞到下一个心跳超时——否则每次请求尾部
    会凭空多出 HEARTBEAT_INTERVAL 的延迟（压测 P99 直接打满该值）。
    """
    try:
        final: dict[str, Any] = {}
        async for chunk in graph.astream(state, cfg, stream_mode="updates"):
            if "__interrupt__" in chunk:
                box["interrupt"] = True
                return
            for _node, update in chunk.items():
                if isinstance(update, dict):
                    final.update(update)
        box["final"] = final
    except Exception as exc:  # noqa: BLE001
        box["error"] = exc
    finally:
        queue.put_nowait(None)  # 哨兵：图已结束


async def _resume_graph(
    graph: Any, cfg: dict[str, Any], value: str, box: dict[str, Any], queue: asyncio.Queue[str]
) -> None:
    """后台续跑挂起的图（HITL 恢复）。与 `_run_graph` 同样的哨兵收尾语义。"""
    try:
        async for chunk in graph.astream(Command(resume=value), cfg, stream_mode="updates"):
            if "__interrupt__" in chunk:
                box["interrupt"] = True
                return
            for _node, update in chunk.items():
                if isinstance(update, dict):
                    box.setdefault("final", {}).update(update)
    except Exception as exc:  # noqa: BLE001
        box["error"] = exc
    finally:
        queue.put_nowait(None)


async def _read_thread(graph: Any, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """从 checkpoint 读最新状态。"""
    try:
        state = await graph.aget_state(cfg)
    except Exception:  # noqa: BLE001 — 无法读取视为无 checkpoint
        return None
    if state is None or not state.values:
        return None
    return {"thread_id": cfg["configurable"]["thread_id"], "state": state.values}


app = create_app()