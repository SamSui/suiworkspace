"""Embedding 服务封装（增量 4 交付）。

设计基线 BGE / text-embedding 系列；模型选型与批大小在增量 4 定稿，
维度须与 `MILVUS_DIM` 和 `db/schemas_milvus.build_collection_schema` 一致。
"""

from __future__ import annotations

from core.exceptions import NotImplementedYet

_INCREMENT = "增量 4（ingest 摄入）"


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化。批大小与超时重试在增量 4 落地。"""
    raise NotImplementedYet(_INCREMENT, "Embedding 服务尚未接入")


async def embed_query(text: str) -> list[float]:
    """单条 query 向量化（检索路径用，须与建库同一模型）。"""
    raise NotImplementedYet(_INCREMENT, "Embedding 服务尚未接入")
