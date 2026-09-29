"""retrieval_service — 进程组 ③：独立混合检索服务（P1.2 落地）。

把编排进程内部的 RAG 检索（ES BM25 + Milvus 向量 → 融合去重 → 重排 → TopN，
连带 embed / 检索缓存键）抽成独立 HTTP 服务，可独立水平扩展。

- 业务核心复用共享的 `core/retrieval.hybrid_search`（与编排内嵌路径同一实现，
  保证 TopN 语义一致）。
- 对外契约：`POST /v1/retrieval`、`POST /v1/embed`、`GET /healthz`。
- 编排进程经 `RetrievalClient` 调用本服务（`core.config.app.retrieval_service_url`）。

本进程只依赖 `core`（存储/配置），不 import `langgraph_service`——真正解耦可扩展。
"""

from __future__ import annotations

# 窄接口：客户端只在编排进程侧实例化使用
from .client import RetrievalClient

__version__ = "0.1.0"

__all__ = ["RetrievalClient", "__version__"]