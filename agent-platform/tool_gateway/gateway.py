"""Tool 网关核心（P1.3 · SUIG-33）。

`ToolGateway` 提供对 HTTP / MCP / SQL 工具的统一准入链，一次调用严格按序执行：
1. **授权**（ToolRegistry 白名单）——未授权一律拒，并**落审计（tool.denied）**；
2. **参数校验**（params_schema）；
3. **限流**（每 actor 每分钟调用数上限，复用 llm_gateway.quota 窗口）；
4. **执行**（按 tool_type 选执行器；SQL 走默认禁 + 最小权限执行器）；
5. **审计**（tool.call 成功/失败，同样 `tool.call`）。

「未授权调用被拒 + 拒绝/调用都落审计」是本轮验收主线的落点。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any

from core.logging import get_logger
from llm_gateway.audit import AuditRecord, AuditWriter, MemoryAuditBackend
from llm_gateway.quota import MemoryQuotaBackend, PolicyQuotaManager, QuotaPolicy

from .errors import ToolRejected, ToolUnavailable
from .executors import ToolExecutor
from .models import ToolCallResponse, ToolSpec
from .registry import ToolRegistry

logger = get_logger(__name__)


# JSON-Schema 式简单校验：{字段名: "str"|"int"|"float"|"bool"|"list"|"dict"}
_TYPE_CHECK = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, list),
    "dict": lambda v: isinstance(v, dict),
}


def validate_params(spec: ToolSpec, params: dict[str, Any]) -> tuple[bool, str]:
    """按 `params_schema` 校验：必填存在 + 类型匹配。未指 schema 放行。"""
    schema = spec.params_schema
    if not schema:
        return True, ""
    if not isinstance(params, dict):
        return False, "参数必须是对象"
    for field, type_name in (schema or {}).items():
        if field not in params:
            return False, f"缺少必填参数: {field}"
        checker = _TYPE_CHECK.get(type_name, lambda v: True)
        if not checker(params[field]):
            return False, f"参数 {field} 类型应为 {type_name}"
    return True, ""


class ToolGateway:
    """工具统一准入编排：授权 → 校验 → 限流 → 执行 → 审计。"""

    def __init__(
        self,
        registry: ToolRegistry,
        executors: dict[str, ToolExecutor],
        writer: AuditWriter | None = None,
        *,
        default_actor_type: str = "agent",
        default_actor_id: str = "tool-gateway",
        rate_policy: QuotaPolicy | None = None,
        ip_factory: Callable[[], str | None] | None = None,
        rate_backend: Any | None = None,
    ) -> None:
        self._registry = registry
        self._executors = dict(executors) if executors else {}
        self._writer = writer or MemoryAuditBackend()
        self._default_actor_type = default_actor_type
        self._default_actor_id = default_actor_id
        self._ip_factory = ip_factory or (lambda: None)
        policies = [p for p in [rate_policy] if p is not None]
        self._rates = PolicyQuotaManager(policies, rate_backend or MemoryQuotaBackend())

    @property
    def rate_manager(self) -> PolicyQuotaManager:
        return self._rates

    def registry(self) -> ToolRegistry:
        """返回工具注册表（供外部注入/查询）。"""
        return self._registry

    def approve(self, actor: str, tool_name: str) -> None:
        """显式授权该 actor 使用某工具（白名单放行）。"""
        self._registry.approve(actor, tool_name)

    # ---------- 统一准入 + 执行 ----------

    async def call(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        actor_type: str | None = None,
        actor_id: str = "",
        run_ref: str | None = None,
        tenant_id: int = 0,
        client_ip: str | None = None,
    ) -> ToolCallResponse:
        actor = actor_id or self._default_actor_id
        atype = actor_type or self._default_actor_type
        ip = client_ip or self._ip_factory()

        spec = self._registry.get(tool_name)
        if spec is None:
            raise ToolUnavailable(f"工具不存在: {tool_name}")

        # 1) 授权白名单（默认拒）
        if not self._registry.can(actor, tool_name):
            self._audit(atype, actor, "tool.denied", spec, ip,
                        diff={"reason": "not_authorized", "tool": tool_name}, success=False)
            raise ToolRejected(f"未授权调用工具: {tool_name}")

        # 2) 参数校验
        ok, reason = validate_params(spec, params)
        if not ok:
            self._audit(atype, actor, "tool.denied", spec, ip,
                        diff={"reason": reason, "tool": tool_name}, success=False)
            raise ToolRejected(f"参数校验失败: {reason}")

        # 3) 限流
        rate_reason = self._rate_denied_reason(actor)
        if rate_reason:
            self._audit(atype, actor, "tool.ratelimited", spec, ip,
                        diff={"reason": rate_reason, "tool": tool_name}, success=False)
            raise ToolRejected(f"工具调用限流: {rate_reason}")

        # 4) 执行
        executor = self._executors.get(spec.tool_type)
        if executor is None:
            raise ToolRejected(f"不支持的执行器: {spec.tool_type}")
        self._rates.enforce(actor, 0)  # 计入一次调用配额
        result = await executor.execute(spec, params)

        # 5) 审计（成功/失败都落）
        diff: dict[str, Any] = {"tool": tool_name, "ok": result.ok, "run_ref": run_ref}
        if result.error:
            diff["error"] = result.error
        self._audit(atype, actor, "tool.call", spec, ip, diff=diff, success=result.ok)
        return ToolCallResponse(
            tool=tool_name,
            ok=result.ok,
            output=result.output if result.ok else None,
            error=result.error if not result.ok else None,
            audit_run_id=f"tool-{secrets.token_hex(4)}",
        )

    # ---------- 内部 ----------

    def _audit(self, atype: str, actor: str, action: str, spec: ToolSpec,
               client_ip: str | None, *, diff: dict[str, Any] | None, success: bool) -> None:
        record = AuditRecord(
            actor_type=atype,
            actor_id=actor,
            action=action,
            resource_type=f"tool:{spec.tool_type}",
            resource_id=spec.name,
            diff_context=diff or {},
            ip=client_ip,
            created_at=datetime.now(),
            run_status=1 if success else 2,
        )
        try:
            self._writer.append(record)
        except Exception as exc:  # noqa: BLE001 — 审计失败不阻断主链路
            logger.warning("audit append failed", extra={"extra_fields": {"error": str(exc)}})

    def _rate_denied_reason(self, actor: str) -> str | None:
        ok, _ = self._rates.check(actor, 0, bump=False)
        if not ok:
            return "窗口调用数超限"
        return None


__all__ = ["ToolGateway"]