"""api — 进程组 ①：FastAPI 网关注入服务。

对外唯一入口。只做鉴权 / 限流 / 校验 / SSE 透传，不直接访问 Milvus / ES / LLM。
"""

__version__ = "0.1.0"
