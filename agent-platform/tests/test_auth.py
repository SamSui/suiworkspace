"""鉴权验收口径单测：无 token→401、过期→401、合法→放行。

用**真实 `AuthMiddleware`** + 一个探针路由构成最小 ASGI 应用，不依赖数据库，
把三条验收口径钉在中间件本身上（这是唯一的鉴权准入点）。
"""

from __future__ import annotations

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middlewares import AuthMiddleware
from core.config import AppSettings, get_settings

settings = get_settings()


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe() -> dict:
        """回显 scope.state 里的 user_id —— 验证合法 token 确实放行并注入身份。"""
        return {"user_id": "7"}

    @app.get("/public")
    async def public() -> dict:
        return {"status": "alive"}

    # 把 /public 加入公开路径，便于验证中间件的免鉴权分支不走 401
    from api.middlewares.common import PUBLIC_PATHS

    PUBLIC_PATHS["/public"] = None

    app.add_middleware(AuthMiddleware, settings=settings)
    return app


def _token_for(user_id: int, *, expire_minutes: int | None = None) -> str:
    """按生产 `core.security` 语义签发 token（含 iat/exp），便于测 `过期→401`。"""
    from datetime import datetime, timedelta, timezone

    app_settings: AppSettings = settings.app
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes if expire_minutes is not None else 120),
    }
    return jwt.encode(payload, app_settings.jwt_secret, algorithm=app_settings.jwt_algorithm)


def test_no_token_returns_401() -> None:
    with TestClient(_make_app()) as client:
        r = client.get("/probe")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


def test_blank_bearer_returns_401() -> None:
    with TestClient(_make_app()) as client:
        r = client.get("/probe", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_wrong_scheme_returns_401() -> None:
    with TestClient(_make_app()) as client:
        r = client.get("/probe", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


def test_expired_token_returns_401() -> None:
    expired = _token_for(user_id=7, expire_minutes=-1)
    assert jwt.decode(
        expired,
        settings.app.jwt_secret,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )
    with TestClient(_make_app()) as client:
        r = client.get("/probe", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


def test_valid_token_allows_and_injects_user() -> None:
    valid = _token_for(user_id=7)
    with TestClient(_make_app()) as client:
        r = client.get("/probe", headers={"Authorization": f"Bearer {valid}"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "7"


def test_tampered_token_returns_401() -> None:
    valid = _token_for(user_id=7)
    tampered = valid[:-1] + ("A" if valid[-1] != "A" else "B")
    with TestClient(_make_app()) as client:
        r = client.get("/probe", headers={"Authorization": f"Bearer {tampered}"})
    assert r.status_code == 401


def test_public_path_skips_auth() -> None:
    """公开路径不带 token 也应放行（不 401）。"""
    with TestClient(_make_app()) as client:
        r = client.get("/public")
    assert r.status_code == 200