"""HITL 挂起节点（增量 3.6 落地）。

挂起点放在 generate 之前：命中 `hitl_required` 时，节点调用 langgraph 的
`interrupt()`——这是框架原生 HITL 原语，会**把图连同当前状态一并落 checkpoint**
（RedisSaver），进程重启后凭 `thread_id` 仍可从同一挂起点恢复（验收 3.6）。

恢复走 `/v1/resume` → `graph.invoke(None, config, Command(resume=value))`：
langgraph 重新执行本节点，`interrupt()` 返回 resume 值 `verdict`。
- `approve` → 放行进 generate；
- 其它（拒绝）→ 置 `aborted=True` 短路生成。
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from core.logging import get_logger
from langgraph_service.graph.state import AgentState

logger = get_logger(__name__)


async def hitl_gate(state: AgentState) -> dict[str, Any]:
    if not state.get("hitl_required"):
        return {}

    verdict = interrupt(
        {"reason": "human_approval", "payload": {"query": state.get("query", "")}}
    )
    # 以下代码仅在 resume 后执行
    decision = str(verdict or "").lower()
    logger.info(
        "HITL resumed",
        extra={"extra_fields": {"verdict": decision, "thread_id": state.get("thread_id")}},
    )
    if decision in {"approve", "yes", "ok", "true", "approved"}:
        return {"hitl_verdict": decision, "hitl_required": False}
    # 拒绝/取消：短路 generate
    return {
        "hitl_verdict": decision or "reject",
        "aborted": True,
        "hitl_required": False,
        "answer": "该请求已被人工拦截（HITL 拒绝），未调用生成。",
        "citations": [],
    }


__all__ = ["hitl_gate"]