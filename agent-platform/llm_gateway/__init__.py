"""LLM 网关（P1.3 · SUIG-33）。

独立 FastAPI 服务，在既有 `langgraph_service/llm/client.py`（重试 / 熔断 / 多
provider 兜底）之上补齐网关级能力：
- 多 provider 路由（按配置装配 provider 链，主挂自动切备）；
- API Key 管理（网关自身以准入 Key 区分调用方，不落明文）；
- 配额（token / 调用次数，可插拔后端）；
- Token 统计（每次调用累加，供调用方自助查询与审计）；
- 调用审计（AuditWriter → `agent_run` / `audit_log`，表契约对齐 P1.1 域模型）。

对外只暴露 `LlmGateway`。编排 / 业务方必须经此层调 LLM —— 配额、统计、
审计都收敛在这里，不重复实现。
"""

from llm_gateway.gateway import LLMGateway

__all__ = ["LLMGateway"]