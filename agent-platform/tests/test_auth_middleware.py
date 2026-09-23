"""AuthMiddleware 验收测试（增量 2 · 2.1 三条验收口径）。

三条口径：
1. 无 token                → 401
2. 过期 token              → 401
3. 合法 token              → 放行进入路由

在中间件层面用最小 ASGI 应用验证，不依赖存储/应用 lifespan。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from api.middlewares.auth import AuthMiddleware
from core.config import Settings
from core.security import create_access_token
from tests._constants import TEST_JWT_SECRET


def build_app(secret: str = TEST_JWT_SECRET) -> tuple[TestClient, Settings]:
    """拼装带 AuthMiddleware 的最小应用，暴露一个受保护路由。"""
    settings = Settings()
    settings.app.jwt_secret = secret

    async def protected(request: Any) -> JSONResponse:
        user_id = (request.scope.get("state") or {}).get("user_id")
        return JSONResponse({"ok": True, "user_id": user_id})

    app = Starlette()
    app.add_route("/protected", protected, methods=["GET"])
    app.add_middleware(AuthMiddleware, settings=settings)

    with TestClient(app) as client:
        return client, settings


def _expired_token(settings: Settings, user_id: int = 1) -> str:
    """用正确密钥签发一个 exp 已落在过去的 token。"""
    now = dt.datetime.now(dt.timezone.utc)
    claims = {
        "sub": str(user_id),
        "iat": now - dt.timedelta(hours=2),
        "exp": now - dt.timedelta(hours=1),
        "type": "access",
    }
    return jwt.encode(claims, settings.app.jwt_secret, algorithm=settings.app.jwt_algorithm)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},  # 有 scheme 无 token
    ],
)
def test_no_token_returns_401(headers: dict[str, str]) -> None:
    client, _ = build_app()
    resp = client.get("/protected", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthenticated"


def test_expired_token_returns_401() -> None:
    client, settings = build_app()
    token = _expired_token(settings)
    resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthenticated"


def test_valid_token_passes() -> None:
    client, settings = build_app()
    token = create_access_token(1, settings.app)
    resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    # scope.state.user_id 已写入，供下游依赖取用
    assert resp.json()["user_id"] == "1"


def test_valid_token_for_other_user_passes() -> None:
    """合法 token 携带正确的 sub，进入路由后 user_id 与签名 identity 一致。"""
    client, settings = build_app()
    token = create_access_token(42, settings.app)
    resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "42"


def test_garbage_token_returns_401() -> None:
    client, _ = build_app()
    resp = client.get("/protected", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401


def test_wrong_secret_token_returns_401() -> None:
    """别处密钥签的 token 被拒——防跨环境复用。"""
    client, _ = build_app(secret="z" * 40)
    foreign = jwt.encode(
        {"sub": "1"}, "other-secret-other-secret-other-secret-xy", algorithm="HS256"
    )
    resp = client.get("/protected", headers={"Authorization": f"Bearer {foreign}"})
    assert resp.status_code == 401