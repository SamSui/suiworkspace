"""Tool 网关服务入口（P1.3 · SUIG-33）。

独立可部署的 FastAPI 服务。启动：
    uvicorn tool_gateway.main:app --host 0.0.0.0 --port 8300

契约（内网 / 经主网关调用方）：
- `POST /v1/tools/execute`  统一工具调用（ToolCallRequest）。
- `GET  /v1/tools`          列出某 actor 可见（已授权）工具。
- `POST /v1/tools/approve`  显式白名单放行某工具（SQL 等默认禁类，供人工/配置放行）。
- `GET  /healthz`          探活。

工具定义与授权当前以默认空白名单启动（默认禁），从配置/运行时（P1.1 `tool` /
`tool_permission` 表）灌入由主应用装配；本入口保持最小骨架，核心闭环在 `gateway.py`
单测覆盖。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.config import get_settings as get_core_settings
from core.logging import get_logger, setup_logging
from llm_gateway.audit import MemoryAuditBackend

from .errors import GatewayError, GatewayNotExist, ToolRejected
from .executors import HttpToolExecutor, McpToolExecutor, SqlToolExecutor
from .gateway import ToolGateway
from .models import ToolCallRequest, ToolCallResponse
from .registry import ToolRegistry

logger = get_logger(__name__)
APP_VERSION = "0.1.0"


def _build_default_gateway(core_settings) -> ToolGateway:
    """装配带默认空白名单 + 三类执行器的最小网关。

    - HTTP：无 host 白名单 → 默认全部禁（fail-closed，防 SSRF）；
    - SQL：无受限 DSN → 默认禁；
    - MCP：进程内 mock。
    工具注册面留空，供外部/后续按需 `registry.register(...)` + `approve(...)`。
    """
    registry = ToolRegistry()
    executors = {
        "http": HttpToolExecutor(host_allowlist=set()),
        "mcp": McpToolExecutor(),
        "sql": SqlToolExecutor(dsns={}),
    }
    return ToolGateway(registry, executors, writer=MemoryAuditBackend())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    core = get_core_settings()
    setup_logging(core.app.log_level, json_output=core.app.is_prod)
    gateway = _build_default_gateway(core.app.is_prod)
    app.state.gateway = gateway
    logger.info("tool-gateway started")
    try:
        yield
    finally:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="智能体中台 · Tool 网关",
        version=APP_VERSION,
        description="HTTP / MCP / SQL 工具统一准入与隔离（P1.3）",
        lifespan=lifespan,
    )

    @app.exception_handler(GatewayError)
    async def _err(_request: Request, exc: GatewayError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
        )

    @app.get("/healthz")
    async def healthz(incoming: Request) -> JSONResponse:
        gw = getattr(incoming.app.state, "gateway", None)
        return JSONResponse(
            content={"status": "ok" if gw is not None else "unavailable", "version": APP_VERSION},
            status_code=200 if gw is not None else 503,
        )

    @app.post("/v1/tools/execute", response_model=ToolCallResponse)
    async def execute(payload: ToolCallRequest, incoming: Request) -> ToolCallResponse:
        gw: ToolGateway = _ready_gateway(incoming)
        return await gw.call(
            payload.tool,
            payload.params,
            actor_type=payload.actor_type,
            actor_id=payload.actor_id,
            run_ref=payload.run_ref,
            tenant_id=payload.tenant_id,
        )

    @app.get("/v1/tools")
    async def list_tools(incoming: Request, actor_id: str | None = None) -> JSONResponse:
        gw: ToolGateway = _ready_gateway(incoming)
        tools = gw.registry().list(actor_id)
        return JSONResponse(content={"tools": [t.model_dump() for t in tools]})

    @app.post("/v1/tools/approve")
    async def approve(body: dict, incoming: Request) -> JSONResponse:
        gw: ToolGateway = _ready_gateway(incoming)
        actor = body.get("actor_id") or ""
        tool = body.get("tool") or ""
        if not actor or not tool:
            raise ToolRejected("approve 需 actor 与 tool")
        gw.approve(actor, tool)
        return JSONResponse(content={"ok": True, "actor": actor, "tool": tool})

    def _ready_gateway(incoming: Request) -> ToolGateway:
        gw = getattr(incoming.app.state, "gateway", None)
        if gw is None:
            raise GatewayNotExist("Tool 网关未就绪")
        return gw

    return app


app = create_app()