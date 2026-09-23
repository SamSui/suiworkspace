"""Milvus Collection schema 与索引定义。

对应《TechnicalDesign》§2.2.2：
- 只存向量 + 主键 + 标量过滤字段，**不存原文**；
- 按 `kb_id` 过滤（分区键在后续增量按租户规模启用）；
- 索引 HNSW + COSINE，与 `MilvusStore.search` 的 metric_type 必须一致。
"""

from __future__ import annotations

from typing import Any

COLLECTION_DESCRIPTION = "知识库切片向量（原文在 ES，本集合只存向量与回链主键）"

# 标量字段最大长度，与 MySQL/ES 侧 id 约定对齐
ID_MAX_LENGTH = 64


def build_collection_schema(datatype: Any, *, dim: int = 768) -> Any:
    """构建 CollectionSchema。

    `datatype` 由调用方传入 `pymilvus.DataType`——避免在模块导入期就强依赖 pymilvus，
    便于在没有安装 Milvus SDK 的环境里做静态检查。
    """
    from pymilvus import CollectionSchema, FieldSchema

    fields = [
        FieldSchema(
            name="chunk_id",
            dtype=datatype.VARCHAR,
            is_primary=True,
            max_length=ID_MAX_LENGTH,
            description="切片全局唯一 ID，与 ES _id 一致",
        ),
        FieldSchema(name="kb_id", dtype=datatype.VARCHAR, max_length=ID_MAX_LENGTH),
        FieldSchema(name="doc_id", dtype=datatype.VARCHAR, max_length=ID_MAX_LENGTH),
        FieldSchema(
            name="content_len",
            dtype=datatype.INT32,
            description="原文 token 数，用于过滤异常切片",
        ),
        FieldSchema(name="embedding", dtype=datatype.FLOAT_VECTOR, dim=dim),
    ]
    return CollectionSchema(fields=fields, description=COLLECTION_DESCRIPTION)


def build_index_params(index_params: Any) -> Any:
    """HNSW + COSINE。参数取召回率/内存平衡的常规起点，上线后按评测调。"""
    index_params.add_index(
        field_name="embedding",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    return index_params


def build_search_params(*, ef: int = 64) -> dict[str, Any]:
    """检索期参数。ef 越大召回越准、越慢——与设计文档 §4 的 topK 控制配套。"""
    return {"metric_type": "COSINE", "params": {"ef": ef}}
