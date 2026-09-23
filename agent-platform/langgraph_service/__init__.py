"""langgraph_service — 进程组 ②：LangGraph 独立编排服务。

对外只暴露内网 HTTP+SSE（裁决 #1），由 `api` 网关调用。
与网关拆开的原因：图执行是有状态长会话，内嵌会阻塞网关 worker。
"""

__version__ = "0.1.0"
