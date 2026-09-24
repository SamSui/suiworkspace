"""文档上传 / 任务查询 / Agent 配置端到端集成测试（增量 2.4/2.5 验收口径）。

需 live MySQL + Redis + FastAPI 全栈 up（与 test_kb_integration 同栈）。
覆盖验收：
- 上传：合法文件 → 202 且 `document(status=0)` 入队；类型超限 → 4xx；无 token → 401。
- 任务查询：`/v1/task/{id}` 状态与 `document.status` 一致；无任务/无 token → 404/401。
- 越权访问他人 kb 的文档/任务/agent → 404（不泄漏存在性）。
MySQL 不可达时整体 skip。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import text

from core.config import get_settings
from core.storage import StorageContainer

_BAD_EXT = ("malware.exe", b"MZ\x90\x00evil", "application/octet-stream")
_TXT = ("doc.txt", "Hello 增量 2.4".encode(), "text/plain")


@pytest.fixture(scope="module")
def mysql_ready() -> bool:
    settings = get_settings()
    container = StorageContainer(settings)
    try:
        import asyncio

        async def _probe() -> bool:
            await container.startup(bootstrap=False, fail_fast=True)
            try:
                async with container.mysql.engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                return True
            finally:
                await container.shutdown()

        return asyncio.run(_probe())
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def app_client(mysql_ready: bool):
    if not mysql_ready:
        pytest.skip("MySQL 不可用，跳过文档/任务/Agent 集成测试")
    from fastapi.testclient import TestClient

    from api.main import create_app

    with TestClient(create_app()) as client:
        yield client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_token(client, name: str) -> str:
    r = client.post("/v1/users", json={"name": name})
    assert r.status_code == 201, r.text
    api_key = r.json()["api_key"]
    tok = client.post("/v1/auth/token", json={"name": name, "api_key": api_key})
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


def _makes_kb(client, token: str, name: str) -> int:
    r = client.post("/v1/kb", json={"name": name}, headers=_auth(token))
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def _upload(client, token: str, kb_id: int, fname: str, content: bytes, mime: str):
    return client.post(
        "/v1/doc",
        data={"kb_id": str(kb_id)},
        files={"file": (fname, content, mime)},
        headers=_auth(token),
    )


def test_document_upload_and_task_flow(app_client) -> None:
    ts = int(time.time())
    token_a = _register_and_token(app_client, f"doc_owner_{ts}")
    token_b = _register_and_token(app_client, f"doc_intruder_{ts}")
    kb_id = _makes_kb(app_client, token_a, f"doc_kb_{ts}")

    # 1) 合法上传 -> 202 + status=0
    up = _upload(app_client, token_a, kb_id, *_TXT)
    assert up.status_code == 202, up.text
    body = up.json()
    assert body["kb_id"] == kb_id
    assert body["status"] == 0  # DocumentStatus.PENDING
    assert body["file_name"] == "doc.txt"
    doc_id = int(body["id"])

    # 2) 任务查询：状态与 document.status 一致
    task = app_client.get(f"/v1/task/{doc_id}", headers=_auth(token_a))
    assert task.status_code == 200, task.text
    tj = task.json()
    assert tj["doc_id"] == doc_id
    assert tj["status"] == 0  # 与上传返回的 body["status"] 一致
    assert tj["task_id"] == str(doc_id)

    # 3) 越权读同一任务 -> 404（不泄漏存在性）
    assert app_client.get(f"/v1/task/{doc_id}", headers=_auth(token_b)).status_code == 404

    # 4) 不存在任务 -> 404
    assert app_client.get("/v1/task/999999", headers=_auth(token_a)).status_code == 404
    # 非法 task_id（非数字）同样 404
    assert app_client.get("/v1/task/not-a-number", headers=_auth(token_a)).status_code == 404

    # 5) 无 token -> 401
    assert (
        app_client.post(
            "/v1/doc",
            data={"kb_id": str(kb_id)},
            files={"file": (_TXT[0], _TXT[1], _TXT[2])},
        ).status_code
        == 401
    )
    assert app_client.get(f"/v1/task/{doc_id}").status_code == 401


def test_document_type_validation_and_get_delete(app_client) -> None:
    ts = int(time.time())
    token = _register_and_token(app_client, f"doc_val_{ts}")
    kb_id = _makes_kb(app_client, token, f"doc_val_kb_{ts}")

    # 类型超限 -> 4xx（422 invalid_argument）
    r = _upload(app_client, token, kb_id, *_BAD_EXT)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "invalid_argument"

    # 越权上传到他人 kb -> 404（require_kb_access）
    token_b = _register_and_token(app_client, f"doc_other_{ts}")
    intrude = _upload(app_client, token_b, kb_id, *_TXT)
    assert intrude.status_code == 404

    # 合法上传后可查询
    up = _upload(app_client, token, kb_id, *_TXT)
    assert up.status_code == 202, up.text
    doc_id = int(up.json()["id"])
    assert app_client.get(f"/v1/doc/{doc_id}", headers=_auth(token)).status_code == 200

    # 同文件重复上传 -> 复用同一 doc（uk_kb_hash 去重，仍回 202）
    up2 = _upload(app_client, token, kb_id, *_TXT)
    assert up2.status_code == 202, up2.text
    assert up2.json()["id"] == doc_id

    # 越权读同一文档 -> 404
    assert app_client.get(f"/v1/doc/{doc_id}", headers=_auth(token_b)).status_code == 404

    # 删除 -> 204；删除后再查 -> 404
    assert app_client.delete(f"/v1/doc/{doc_id}", headers=_auth(token)).status_code == 204
    assert app_client.get(f"/v1/doc/{doc_id}", headers=_auth(token)).status_code == 404


def test_agent_config_create_list(app_client) -> None:
    ts = int(time.time())
    token = _register_and_token(app_client, f"agent_owner_{ts}")
    token_b = _register_and_token(app_client, f"agent_intruder_{ts}")
    kb_id = _makes_kb(app_client, token, f"agent_kb_{ts}")

    # 创建
    payload = {"kb_id": kb_id, "name": "rag", "graph_type": "rag_graph"}
    created = app_client.post("/v1/agent", json=payload, headers=_auth(token))
    assert created.status_code == 201, created.text
    cfg = created.json()
    assert cfg["status"] == 1 and cfg["version"] == 1
    assert cfg["kb_id"] == kb_id and cfg["graph_type"] == "rag_graph"

    # 列表（owner）
    lst = app_client.get(f"/v1/agent?kb_id={kb_id}", headers=_auth(token))
    assert lst.status_code == 200
    assert any(c["id"] == cfg["id"] for c in lst.json())

    # 越权访问他人 kb 的 agent 列表/创建 -> 404
    assert (
        app_client.get(f"/v1/agent?kb_id={kb_id}", headers=_auth(token_b)).status_code == 404
    )
    intrude = app_client.post(
        "/v1/agent",
        json={"kb_id": kb_id, "name": "x", "graph_type": "rag_graph"},
        headers=_auth(token_b),
    )
    assert intrude.status_code == 404

    # 无 token -> 401
    assert app_client.get(f"/v1/agent?kb_id={kb_id}").status_code == 401