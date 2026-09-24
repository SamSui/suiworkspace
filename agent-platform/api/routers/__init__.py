"""网关业务路由。

增量 1 实现 `health`；增量 2.1 新增 `auth`（JWT 签发）与 `users`（用户 CRUD + api_key 轮换）；
增量 2.2 实现 `knowledge`（知识库 CRUD + 权限）；增量 2.4/2.5 实现 `document`（上传/查询/删除）、
`task`（任务查询）、`agent`（Agent 配置）。
`chat` 为显式占位——返回 501 并标注所属增量，不伪装成已实现。
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
