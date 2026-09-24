"""OpenAPI 契约断言（增量 2.6）：网关对外路径与状态码齐备。

`create_app()` 只装配路由/中间件，不触发 lifespan（不连存储），可无基础设施运行。
断言增量 2.4/2.5 的对外契约与效果一致的机器可读 schema。
"""

from __future__ import annotations

from api.main import create_app


def _schema() -> dict:
    return create_app().openapi()


def test_openapi_contains_increment2_paths() -> None:
    s = _schema()
    paths = s["paths"]

    # 2.4 文档上传/查询/删除
    assert "post" in paths["/v1/doc"]
    assert "get" in paths["/v1/doc/{doc_id}"]
    assert "delete" in paths["/v1/doc/{doc_id}"]
    # 2.5 任务查询 + Agent 配置
    assert "get" in paths["/v1/task/{task_id}"]
    assert "get" in paths["/v1/agent"]
    assert "post" in paths["/v1/agent"]
    # 既有
    assert "get" in paths["/v1/kb"]


def test_document_upload_returns_202() -> None:
    op = _schema()["paths"]["/v1/doc"]["post"]
    # 契约核心：合法上传 → 202；请求体/表单校验失败 → 422
    assert "202" in op["responses"]
    assert "422" in op["responses"]
    # multipart 上传（Form+File 由 python-multipart 支持）
    assert "multipart/form-data" in op["requestBody"]["content"]


def test_agent_create_returns_201() -> None:
    op = _schema()["paths"]["/v1/agent"]["post"]
    assert "201" in op["responses"]


def test_openapi_is_valid_and_titled() -> None:
    s = _schema()
    assert s["info"]["title"]
    assert s["openapi"].startswith("3.")