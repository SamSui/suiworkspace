"""Tool 网关数据契约（P1.3 · SUIG-33）。

工具（HTTP / MCP / SQL）统一准入所需的入参 / 出参 / 异常建模：
- `ToolSpec` —— 工具定义（对齐 P1.1 `tool` 表：name / tool_type / endpoint /
  params_schema / isolated）；
- `ToolCallRequest` —— 一次调用入参（调用方、参数、审计关联）；
- `ToolCallResult` —— 调用出参（含 usage 之外的执行结果 / 错误与 audit）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ToolKind = Literal["http", "mcp", "sql"]


class ToolSpec(BaseModel):
    """一个工具的定义。`isolated` 对 SQL/代码类必须为 True（默认禁铁律）。"""

    name: str = Field(min_length=1)
    tool_type: ToolKind = "http"
    # 工具幂等/只读语义——SQL 类工具进一步由最低权限库账号承载
    endpoint: str | None = None
    params_schema: dict[str, Any] | None = None  # JSON-Schema 子集（字段名→校验规则）
    timeout_seconds: float = 10.0
    # SQL / 代码类必须 true；默认拒绝即体现在 isolated 与白名单双重门控
    isolated: bool = False

    @property
    def is_code_or_sql(self) -> bool:
        return self.tool_type == "sql"


class ToolCallRequest(BaseModel):
    """一次工具调用入参。"""

    tool: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    actor_type: str = "agent"
    actor_id: str = ""
    run_ref: str | None = None
    tenant_id: int = 0


class ToolCallResponse(BaseModel):
    """一次工具调用出参。"""

    tool: str
    ok: bool
    output: Any = None
    error: str | None = None
    audit_run_id: str | None = None


class ToolListResponse(BaseModel):
    """已注册工具列表（不泄露敏感字段，如凭据引用）。"""

    tools: list[ToolSpec] = Field(default_factory=list)


__all__ = ["ToolCallRequest", "ToolCallResponse", "ToolKind", "ToolListResponse", "ToolSpec"]