"""冒烟门禁脚本（增量 2 前置 / 增量 2.1 扩展）。

架构裁决《SUIG-10 增量1》第四节四项：
  1. compose 六服务 healthy          —— 另用 `docker compose ps` 验证
  2. 四类存储 connect() + health()    ——本就 low 即 /healthz
  3. FastAPI 启动 + OpenAPI + /healthz 200
  4. AsyncRedisSaver 构造 + asetup() 实跑（含真实写入 roundtrip）

增量 2.1 新增门禁 5（用户与鉴权闭环）：
  5. 注册 → api_key 换 JWT → 无/过期/合法 token 三件套（401/401/200）

运行：
    .\.venv\Scripts\python.exe scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.config import get_settings
from core.logging import setup_logging
from core.storage import StorageContainer


async def gate_storage(container: StorageContainer) -> bool:
    """门禁 2：四类存储 connect() + health()。整体健康即 /healthz 200。"""
    print("\n=== [门禁 2] 四类存储 connect + health() ===")
    await container.startup(bootstrap=True, fail_fast=False)
    healthy, _results, summary = await container.health()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n/healthz -> {'200' if healthy else '503'}  (healthy={healthy})")
    await container.shutdown()
    return healthy


async def gate_app() -> bool:
    """门禁 3：FastAPI 应用启动 + OpenAPI 可访问 + /healthz 200。"""
    print("\n=== [门禁 3] FastAPI 应用 + OpenAPI + /healthz ===")
    try:
        from fastapi.testclient import TestClient

        from api.main import create_app
    except Exception as exc:  # noqa: BLE001
        print(f"IMPORT FAIL: {exc!r}")
        sys.exit(2)

    app = create_app()
    ok = True
    with TestClient(app) as client:
        try:
            r = client.get("/openapi.json")
            paths = list(r.json().get("paths", {}).keys())
            print(f"openapi.json -> {r.status_code}  paths={paths}")
            ok = ok and r.status_code == 200
        except Exception as exc:  # noqa: BLE001
            print(f"openapi FAIL: {exc!r}")
            ok = False

        try:
            r = client.get("/healthz")
            body = r.json()
            print(f"/healthz -> {r.status_code}  healthy={body.get('healthy')} failed={body.get('failed')}")
            ok = ok and r.status_code == 200
        except Exception as exc:  # noqa: BLE001
            print(f"/healthz FAIL: {exc!r}")
            ok = False

        r = client.get("/healthz/live")
        print(f"/healthz/live -> {r.status_code}  {r.json()}")
        ok = ok and r.status_code == 200
    return ok


async def gate_checkpointer() -> bool:
    """门禁 4.2：AsyncRedisSaver 构造 + asetup() + redis write/read roundtrip。"""
    print("\n=== [4.2] AsyncRedisSaver 构造 + asetup() ===")
    try:
        import uuid

        import redis.asyncio as aioredis
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver

        settings = get_settings()
        client = aioredis.from_url(settings.redis.url, decode_responses=False)
        saver = AsyncRedisSaver(redis_client=client)
        await saver.asetup()
        print("asetup() OK")

        # 通过 saver.grpc 底层不可直接测，用同一 client 做一次 redis roundtrip 佐证连接
        key = f"smoke:{uuid.uuid4().hex}"
        await client.set(key, b"ok")
        got = await client.get(key)
        print(f"redis write/read roundtrip -> {got!r}")
        await client.delete(key)

        await client.aclose()
        print("client closed OK")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"checkpointer FAIL: {exc!r}")
        return False


async def gate_auth() -> bool:
    """门禁 5（增量 2.1）：用户与鉴权最小闭环。

    覆盖架构验收口径：注册 → 换 JWT → 合法放行；无 token / 过期 token → 401。
    依赖 `pyjwt`（已是主依赖）+ 标准库，不引入新第三方契约。依赖 live MySQL（引导用户）。
    """
    print("\n=== [门禁 5] 用户与鉴权闭环 ===")
    try:
        import time

        from fastapi.testclient import TestClient

        from api.main import create_app
    except Exception as exc:  # noqa: BLE001
        print(f"IMPORT FAIL: {exc!r}")
        return False

    try:
        app = create_app()
        name = f"smoke_user_{int(time.time())}"
        with TestClient(app) as client:
            # 1) 注册 -> 拿 api_key（明文仅此一次）
            r = client.post("/v1/users", json={"name": name})
            if r.status_code != 201:
                print(f"register -> {r.status_code} {r.text}")
                return False
            api_key = r.json()["api_key"]
            print(f"register -> 201 (user_id={r.json()['user']['id']}, api_key issued)")

            # 2) api_key 换 JWT
            r = client.post("/v1/auth/token", json={"name": name, "api_key": api_key})
            if r.status_code != 200:
                print(f"auth/token -> {r.status_code} {r.text}")
                return False
            token = r.json()["access_token"]
            print(f"auth/token -> 200 (token_type={r.json()['token_type']})")

            # 3) 无 token -> 401
            no_tok = client.get("/v1/users/me")
            ok_no = no_tok.status_code == 401
            print(
                f"no token /v1/users/me -> {no_tok.status_code} (expect 401) "
                f"{'OK' if ok_no else 'FAIL'}"
            )

            # 4) 过期 token -> 401
            from datetime import datetime, timedelta, timezone

            import jwt

            settings = get_settings()
            exp = jwt.encode(
                {
                    "sub": str(r.json()["user"]["id"]),
                    "iat": datetime.now(timezone.utc) - timedelta(hours=1),
                    "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
                },
                settings.app.jwt_secret,
                algorithm=settings.app.jwt_algorithm,
            )
            rr_exp = client.get("/v1/users/me", headers={"Authorization": f"Bearer {exp}"})
            ok_exp = rr_exp.status_code == 401
            print(
                f"expired token -> {rr_exp.status_code} (expect 401) "
                f"{'OK' if ok_exp else 'FAIL'}"
            )

            # 5) 合法 token 放行
            rr_ok = client.get("/v1/users/me", headers={"Authorization": f"Bearer {token}"})
            ok_valid = rr_ok.status_code == 200 and rr_ok.json()["name"] == name
            print(f"valid token -> {rr_ok.status_code} {'OK' if ok_valid else 'FAIL'}")

        return bool(ok_no and ok_exp and ok_valid)
    except Exception as exc:  # noqa: BLE001
        print(f"auth gate FAIL: {exc!r}")
        return False


async def main() -> int:
    setup_logging("INFO", json_output=False)
    settings = get_settings()
    print(f"venv python: {sys.version.split()[0]}")
    print(f"mysql={settings.mysql.host}:{settings.mysql.port} redis={settings.redis.url} "
          f"milvus={settings.milvus.uri} es={settings.es.hosts}")

    container = StorageContainer(settings)

    g2 = await gate_storage(container)
    g3 = await gate_app()
    g4 = await gate_checkpointer()
    g5 = await gate_auth()

    print("\n===== 门禁汇总 =====")
    print(f"门禁 2  四类存储 connect+health  {'PASS' if g2 else 'FAIL'}")
    print(f"门禁 3  FastAPI+OpenAPI+healthz    {'PASS' if g3 else 'FAIL'}")
    print(f"门禁 4.2 AsyncRedisSaver asetup     {'PASS' if g4 else 'FAIL'}")
    print(f"门禁 5  用户与鉴权闭环              {'PASS' if g5 else 'FAIL'}")

    all_ok = g2 and g3 and g4 and g5
    print(f"\nOVERALL: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))