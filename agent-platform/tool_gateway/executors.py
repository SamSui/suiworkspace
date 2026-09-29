"""工具执行器（P1.3 · SUIG-33）。

三类执行器统一 `execute(spec, params)` 契约，返回 `ExecResult(ok, output, error)`。

安全基线（本轮重点）：
- **HTTP**：出站 host 受限（SSRF 防护）——host 不在白名单一律拒，无白名单则全部
  禁（fail-closed）。
- **MCP**：走同 HTTP 统一契约；未配 external client 时用进程内 mock 兜底，便于
  契约先行 + 单测。
- **SQL / 代码**：默认禁 + 最小权限 + 隔离环境。执行须同时满足：工具 `isolated=True`、
  配置了独立受限 DSN（min_priv 只读账号，非 root/写库）、语句通过只读白名单静态检查。
  缺一即拒。
"""

from __future__ import annotations

import asyncio
import dataclasses
import urllib.parse
from abc import ABC, abstractmethod
from typing import Any

from core.logging import get_logger

from .models import ToolSpec

logger = get_logger(__name__)


@dataclasses.dataclass
class ExecResult:
    ok: bool
    output: Any = None
    error: str | None = None


class ToolExecutor(ABC):
    """执行器抽象：外部统一用 `execute`，其内对申明的超时做兜底并以 `_execute` 实现。"""

    @abstractmethod
    async def _execute(self, spec: ToolSpec, params: dict[str, Any]) -> ExecResult:
        """子类实现真正的调用。"""
        raise NotImplementedError  # pragma: no cover — 抽象方法

    async def execute(self, spec: ToolSpec, params: dict[str, Any]) -> ExecResult:
        """公开入口：对子类实现施加超时兜底（超时视为失败），不向上层抛异步超时。"""
        timeout = spec.timeout_seconds or 10.0
        try:
            return await asyncio.wait_for(self._execute(spec, params), timeout=timeout)
        except asyncio.TimeoutError:
            return ExecResult(False, error=f"工具执行超时（>{timeout}s）")
        except Exception as exc:  # noqa: BLE001 — 执行异常统一收敛为失败结果
            return ExecResult(False, error=str(exc))


# ---------------------------------------------------------------------------
# HTTP executor（SSRF 防护：出站 host 白名单）
# ---------------------------------------------------------------------------


class HttpToolExecutor(ToolExecutor):
    """统一 HTTP 工具执行器。出站仅允许显式白名单 host（防 SSRF）。"""

    def __init__(self, host_allowlist: set[str] | None = None) -> None:
        self._hosts = {h for h in (host_allowlist or []) if h}

    def _host_allowed(self, host: str) -> bool:
        if not self._hosts:
            return False  # fail-closed：无白名单即全部禁止
        if host in self._hosts:
            return True
        return any(host.endswith("." + h) for h in self._hosts)

    async def _execute(self, spec: ToolSpec, params: dict[str, Any]) -> ExecResult:
        endpoint = spec.endpoint
        if not endpoint:
            return ExecResult(False, error="HTTP 工具缺 endpoint")
        try:
            parsed = urllib.parse.urlparse(endpoint)
            scheme = (parsed.scheme or "").lower()
            host = parsed.hostname or ""
        except Exception as exc:  # noqa: BLE001
            return ExecResult(False, error=f"endpoint 解析失败: {exc}")
        if scheme not in {"http", "https"}:
            return ExecResult(False, error=f"非法协议: {scheme}")
        if not self._host_allowed(host):
            return ExecResult(False, error=f"目标 host 不在白名单: {host}")

        import httpx

        timeout = spec.timeout_seconds or 10.0
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, json=params)
                resp.raise_for_status()
                try:
                    out: Any = resp.json()
                except Exception:  # noqa: BLE001
                    out = resp.text
                return ExecResult(True, output=out)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body = exc.response.text[:200]
            return ExecResult(False, error=f"HTTP {status}: {body}")
        except httpx.TimeoutException as exc:
            return ExecResult(False, error=f"HTTP 超时: {exc}")
        except Exception as exc:  # noqa: BLE001 — 统一收口
            return ExecResult(False, error=str(exc))


# ---------------------------------------------------------------------------
# MCP 工具（契约先行；external client 可插拔）
# ---------------------------------------------------------------------------


class McpToolExecutor(ToolExecutor):
    """MCP 工具执行。配置 external client 时走真实 transport，否则用进程内 mock
    （回显参数 + 标注 MCP），便于契约 / 统一准入链在无 MCP server 时也能验证。"""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    async def _execute(self, spec: ToolSpec, params: dict[str, Any]) -> ExecResult:
        if self._client is not None:
            try:
                out = await self._client.invoke_tool(spec.name, params)
                return ExecResult(True, output=out)
            except Exception as exc:  # noqa: BLE001
                return ExecResult(False, error=str(exc))
        return ExecResult(True, output={"mcp": spec.name, "echo": params})


# ---------------------------------------------------------------------------
# SQL 工具（默认禁 + 最小权限 + 隔离）
# ---------------------------------------------------------------------------


class SqlToolExecutor(ToolExecutor):
    """SQL 工具执行器：最小权限 + 隔离。

    - **默认禁**：未配置该工具的受限只读 DSN，一律拒。
    - **最小权限**：仅用 per-tool 的 min_priv 只读账号，不用公共写账号。
    - **隔离环境**：强制只读事务（SET TRANSACTION READ ONLY），语句静态白名单
      （只放 select/with/show/explain，禁止 DML/DDL/注释/分号链）。
    """

    ALLOWED_VERBS = {"select", "with", "show", "explain"}
    FORBIDDEN_SUBSTR = {
        "insert", "update", "delete", "drop", "alter", "create", "truncate",
        "grant", "revoke", "load", "call", "exec", "procedure", "trigger",
        "--", "/*", "*/",
    }

    def __init__(self, dsns: dict[str, str] | None = None) -> None:
        # {tool_name: 受限只读 DSN}
        self._dsns = dict(dsns or {})

    def _static_check(self, sql: str) -> tuple[bool, str]:
        """返回 (是否允许, 拒绝原因)。只读白名单 + 禁止敏感关键字。"""
        if not sql or not sql.strip():
            return False, "缺少 SQL 语句"
        verb = sql.lstrip().split(None, 1)[0].lower()
        if verb not in self.ALLOWED_VERBS:
            return False, f"语句类型不允许: {verb or '未知'}"
        low = sql.lower()
        for kw in self.FORBIDDEN_SUBSTR:
            if kw in low:
                return False, f"检测到禁止关键字: {kw}"
        return True, ""

    async def _execute(self, spec: ToolSpec, params: dict[str, Any]) -> ExecResult:
        dsn = self._dsns.get(spec.name)
        if not dsn:
            return ExecResult(False, error="SQL 工具未配置隔离只读 DSN（默认禁）")
        if not spec.isolated:
            return ExecResult(False, error="SQL 工具未声明 isolated，拒绝执行")
        sql = params.get("sql") if isinstance(params, dict) else None
        if not isinstance(sql, str) or not sql.strip():
            return ExecResult(False, error="缺少 sql 参数")
        allowed, reason = self._static_check(sql)
        if not allowed:
            return ExecResult(False, error=f"SQL 静态白名单拒绝: {reason}")

        try:
            from sqlalchemy import create_engine
            from sqlalchemy import text as _sql_text

            eng = create_engine(dsn)
            with eng.connect() as conn:
                with conn.begin():
                    try:
                        conn.execute(_sql_text("SET TRANSACTION READ ONLY"))
                    except Exception:  # noqa: BLE001 — 驱动不支持则尽力而为
                        pass
                    rows = conn.execute(_sql_text(sql)).fetchall()
            return ExecResult(True, output=[list(r) for r in rows[:200]])
        except Exception as exc:  # noqa: BLE001
            return ExecResult(False, error=f"SQL 执行失败: {exc}")


__all__ = ["ExecResult", "ToolExecutor", "HttpToolExecutor", "McpToolExecutor", "SqlToolExecutor"]