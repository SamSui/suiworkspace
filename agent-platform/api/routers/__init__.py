"""网关业务路由。

增量 1 实现 `health`；增量 2.1 新增 `auth`（JWT 签发）与 `users`（用户 CRUD + api_key 轮换）。
其余路由为**显式占位**——返回 501 并标注所属增量，不伪装成已实现。
"""

from api.routers import agent, auth, chat, document, health, knowledge, task, users

__all__ = [
    "agent",
    "auth",
    "chat",
    "document",
    "health",
    "knowledge",
    "task",
    "users",
]
