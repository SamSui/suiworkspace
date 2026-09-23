"""健康探活路由——增量 1 的对外可验证面。

- `GET /healthz`       聚合四类存储，任一不健康 → 503（K8s readiness 用）
- `GET /healthz/live`  进程存活（liveness，不探依赖）
- `GET /healthz/ready` 就绪 = 聚合结果（readiness）
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from api.deps import Container
from api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz(container: Container, response: Response) -> HealthResponse:
    healthy, _results, summary = await container.health()
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(**summary)


@router.get("/healthz/live")
async def liveness() -> dict[str, str]:
    """进程存活即可——依赖故障不应触发容器重启。"""
    return {"status": "alive"}


@router.get("/healthz/ready", response_model=HealthResponse)
async def readiness(container: Container, response: Response) -> HealthResponse:
    healthy, _results, summary = await container.health()
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(**summary)
