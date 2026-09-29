"""Tool 网关（P1.3 · SUIG-33）。

独立 FastAPI 服务，为 HTTP / MCP / SQL 三类工具提供统一准入：
- 授权（ToolRegistry 白名单，未授权一律拒）；
- 参数校验（params_schema）；
- 超时 / 限流；
- 调用审计（成功 / 拒绝 / 失败都留痕，复用 `llm_gateway.audit`）；
- SQL / 代码执行默认禁，仅显式白名单 + 隔离环境（最小权限）放行（见 executors/sql）。

对外只暴露 `ToolGateway`。业务方统一经此层执行工具调用。
"""

from tool_gateway.gateway import ToolGateway

__all__ = ["ToolGateway"]