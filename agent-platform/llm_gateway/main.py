"""LLM 网关服务入口（P1.3 · SUIG-33）。

独立可部署的 FastAPI 服务。启动：
    uvicorn llm_gateway.main:app --host 0.0.0.0 --port 8200

对外契约（内网 / 经主网关调用方）：
- `POST /v1/llm/chat`        非流式补全（body 为 ChatRequest；Header `X-Api-Key`）。
- `GET  /v1/llm/usage/{key}` 调用方累计 token / 调用统计。
- `GET  /v1/llm/quota/{key}` 配额水位。
- `GET  /healthz`           探活。

配额后端默认内存（本机 / 单测）；生产可用 Redis 替换，接口见 `quota.py`。
审计后端内存 / MySQL（依赖 P1.1 表），由 `LLMGW_AUDIT_BACKEND` 选择。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from core.config import get_settings as get_core_settings
from core.logging import get_logger, setup_logging
from langgraph_service.llm.client import LLMClient

from .audit import AuditWriter, build_audit_writer
from .config import get_llm_gateway_settings
from .errors import GatewayError
from .gateway import LLMGateway
from .models import ChatCompletionResponse, ChatRequest
from .quota import MemoryQuotaBackend, PolicyQuotaManager, QuotaPolicy

logger = get_logger(__name__)
APP_VERSION = "0.1.0"


def _build_quota(settings) -> PolicyQuotaManager:
    policies = [
        QuotaPolicy(
            key=str(item["key"]),
            tokens_per_minute=int(item.get("tokens_per_minute") or 0),
            calls_per_minute=int(item.get("calls_per_minute") or 0),
        )
        for item in settings.quota_list
        if isinstance(item, dict) and item.get("key")
    ]
    return PolicyQuotaManager(policies, MemoryQuotaBackend())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_llm_gateway_settings()
    core = get_core_settings()
    setup_logging(core.app.log_level, json_output=core.app.is_prod)

    # LLM client：复用既有 provider 链（重试 / 熔断 / 多 provider 兜底）
    client: LLMClient | None = None
    try:
        client = await LLMClient.from_settings(core.llm)
    except Exception as exc:  # noqa: BLE001 — 装配失败不阻断启动
        logger.warning("llm client 装配失败", extra={"extra_fields": {"error": str(exc)}})

    quota = _build_quota(settings)
    writer: AuditWriter = build_audit_writer(settings.audit_backend, container=None)
    gateway = None
    if client is not None:
        gateway = LLMGateway(
            client,
            quota,
            writer,
            api_key_map=settings.api_key_map,
            default_actor_type=settings.default_actor_type,
            default_actor_id=settings.default_actor_id,
        )
    app.state.gateway = gateway
    app.state.quota = quota
    app.state.writer = writer
    logger.info("llm-gateway started", extra={"extra_fields": {"gateway": gateway is not None}})
    try:
        yield
    finally:
        if gateway is not None:
            await gateway.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="智能体中台 · LLM 网关",
        version=APP_VERSION,
        description="模型统一路由 / 配额 / 统计 / 审计（P1.3）",
        lifespan=lifespan,
    )

    @app.exception_handler(GatewayError)
    async def _gateway_error(_request: Request, exc: GatewayError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "detail": exc.detail,
                    "retry_after": exc.retry_after,
                }
            },
        )

    @app.exception_handler(GatewayNotExist)
    async def _gateway_not_ready(_request: Request, exc: GatewayNotExist) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "unavailable", "message": str(exc)}},
        )

    @app.get("/healthz")
    async def healthz(incoming: Request) -> JSONResponse:
        gw = getattr(incoming.app.state, "gateway", None)
        return JSONResponse(
            content={"status": "ok" if gw is not None else "unavailable", "version": APP_VERSION},
            status_code=200 if gw is not None else 503,
        )

    @app.post("/v1/llm/chat", response_model=ChatCompletionResponse)
    async def chat(
        request: ChatRequest,
        incoming: Request,
        x_api_key: str | None = Header(default=None),
    ) -> ChatCompletionResponse:
        gw: LLMGateway = _gateway(incoming)
        client_ip = incoming.client.host if incoming.client else None
        return await gw.chat(request, x_api_key, client_ip=client_ip)

    @app.get("/v1/llm/usage/{key}")
    async def usage(key: str, incoming: Request) -> JSONResponse:
        return JSONResponse(content=_gateway(incoming).usage_metrics(key))

    @app.get("/v1/llm/quota/{key}")
    async def quota(key: str, incoming: Request) -> JSONResponse:
        return JSONResponse(content=_gateway(incoming).quota_snapshot(key))

    def _gateway(incoming: Request) -> LLMGateway:
        gw = getattr(incoming.app.state, "gateway", None)
        if gw is None:
            raise GatewayNotExist("LLM 网关未就绪（provider 装配失败）")
        return gw

    return app


class GatewayNotExist(Exception):
    """网关未就绪。"""


app = create_app()