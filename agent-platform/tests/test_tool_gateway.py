"""P1.3 · Tool 网关单测（SUIG-33）。

Step2+Step3 验收口径：
- 工具经统一准入，未授权调用被拒，拒绝/调用都落审计；
- 参数校验、超时、限流生效；
- SQL 工具默认禁；白名单内以最小权限、隔离执行（无受限 DSN 拒、DML/DDL 拒、
  SELECT 白名单放行）；
- HTTP 出站 SSRF 防线（host 白名单 fail-closed）。

全链路内存后端 + 桩执行器，不依赖数据库/SQL server。
"""

from __future__ import annotations

import asyncio

from llm_gateway.audit import MemoryAuditBackend
from tool_gateway.errors import ToolRejected
from tool_gateway.executors import (
    ExecResult,
    HttpToolExecutor,
    McpToolExecutor,
    SqlToolExecutor,
    ToolExecutor,
)
from tool_gateway.gateway import ToolGateway
from tool_gateway.models import ToolSpec
from tool_gateway.registry import ToolRegistry


class _SlowExecutor(ToolExecutor):
    """超时桩。"""

    async def _execute(self, spec, params):
        await asyncio.sleep(5)
        return ExecResult(True, output="late")


class _DenyRate:
    """恒拒限流桩。"""

    def check(self, key, tokens, *, bump=False):
        return False, 2.0

    def enforce(self, key, tokens):
        pass

    def snapshot(self, key):
        return {}


def _registry(specs, allow):
    reg = ToolRegistry()
    for s in specs:
        reg.register(s)
    for actor, tools in allow.items():
        for t in tools:
            reg.approve(actor, t)
    return reg


def _gateway(specs, allow, *, http_hosts=None, sql_dsns=None):
    reg = _registry(specs, allow)
    executors = {
        "http": HttpToolExecutor(host_allowlist=http_hosts),
        "mcp": McpToolExecutor(),
        "sql": SqlToolExecutor(dsns=sql_dsns or {}),
    }
    writer = MemoryAuditBackend()
    return ToolGateway(reg, executors, writer=writer, default_actor_id="alice"), writer


_HTTP = ToolSpec(name="http", tool_type="http", endpoint="https://repo/api", timeout_seconds=5)
_MCP = ToolSpec(name="files", tool_type="mcp")
_SQL = ToolSpec(name="report_db", tool_type="sql", isolated=True)


# ---------- 未授权被拒 + 审计 ----------

async def test_unauthorized_call_rejected_and_audited():
    gw, writer = _gateway([_HTTP], allow={})
    try:
        await gw.call("http", {"x": 1}, actor_id="alice")
        raise AssertionError("应拒绝未授权")
    except ToolRejected:
        pass
    assert any(r.action == "tool.denied" and r.run_status == 2 for r in writer.records)


# ---------- 授权 + 执行 + 审计 ----------

async def test_authorized_call_executes_and_lands_audit():
    # 用 MCP mock（无网络）验证：授权 → 执行 → 审计 tool.call
    gw, writer = _gateway([_MCP], allow={"alice": ["files"]})
    resp = await gw.call("files", {}, actor_id="alice")
    assert resp.ok
    assert any(r.action == "tool.call" for r in writer.records)


# ---------- 参数校验 ----------

async def test_param_validation_rejects():
    spec = ToolSpec(name="calc", tool_type="http", params_schema={"a": "int", "b": "str"})
    gw, writer = _gateway([spec], allow={"alice": ["calc"]})
    try:
        await gw.call("calc", {"a": "not-int"}, actor_id="alice")
        raise AssertionError("应拒绝参数错误")
    except ToolRejected:
        pass
    assert any(r.action == "tool.denied" for r in writer.records)


# ---------- HTTP SSRF fail-closed ----------

async def test_http_fail_closed_without_host_allowlist():
    gw, _ = _gateway([_HTTP], allow={"alice": ["http"]}, http_hosts=set())
    resp = await gw.call("http", {"x": 1}, actor_id="alice")
    assert not resp.ok
    assert "白名单" in (resp.error or "")


# ---------- 超时 ----------

async def test_executor_timeout_surfaces_as_failure():
    spec = ToolSpec(name="slow", tool_type="http", endpoint="https://x", timeout_seconds=0.05)
    reg = _registry([spec], {"alice": ["slow"]})
    writer = MemoryAuditBackend()
    gw = ToolGateway(reg, {"http": _SlowExecutor()}, writer=writer, default_actor_id="alice")
    resp = await gw.call("slow", {}, actor_id="alice")
    assert not resp.ok
    assert "超时" in (resp.error or "")


# ---------- 限流 ----------

async def test_rate_limit_rejects_excess():
    gw, _ = _gateway([_MCP], allow={"alice": ["files"]})
    gw._rates = _DenyRate()
    try:
        await gw.call("files", {}, actor_id="alice")
        raise AssertionError("应限流")
    except ToolRejected:
        pass


# ---------- SQL：默认禁 / 最小权限 / 隔离 ----------

async def test_sql_default_denied_without_dsn():
    gw, _ = _gateway([_SQL], allow={"alice": ["report_db"]}, sql_dsns={})
    resp = await gw.call("report_db", {"sql": "SELECT 1"}, actor_id="alice")
    assert not resp.ok
    assert "默认" in (resp.error or "")


async def test_sql_dml_denied_by_static_check():
    gw, _ = _gateway(
        [_SQL],
        allow={"alice": ["report_db"]},
        sql_dsns={"report_db": "mysql+pymysql://ro:pw@db/report"},
    )
    resp = await gw.call("report_db", {"sql": "DROP TABLE users"}, actor_id="alice")
    assert not resp.ok
    assert "拒绝" in (resp.error or "")


async def test_sql_readonly_select_passes_static_check():
    gw, _ = _gateway(
        [_SQL],
        allow={"alice": ["report_db"]},
        sql_dsns={"report_db": "mysql+pymysql://ro:pw@db/report"},
    )
    resp = await gw.call("report_db", {"sql": "SELECT 1"}, actor_id="alice")
    # SELECT 通过静态白名单；真实 DB 未配置连接 → 结果应为「执行失败」而非「被静态拒」
    assert resp.ok or (resp.error and "执行失败" in (resp.error or ""))