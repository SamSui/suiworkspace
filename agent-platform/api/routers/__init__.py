"""网关业务路由。

增量 1 只实现 `health`；其余四个路由为**显式占位**——返回 501 并标注所属增量，
不伪装成已实现。它们的接口签名即对外契约草案，供增量 2 评审。
"""

from api.routers import agent, chat, document, health, knowledge, task

__all__ = ["agent", "chat", "document", "health", "knowledge", "task"]
