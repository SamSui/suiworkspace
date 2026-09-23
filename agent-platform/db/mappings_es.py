"""Elasticsearch 索引 mapping。

对应《TechnicalDesign》§2.2.3：存 chunk 原文与过滤字段。
`kb_id` / `doc_id` 用 keyword（term 过滤 + 聚合），`text` 走 BM25，`title` 额外做 ngram 提升短词命中。
"""

from __future__ import annotations

from typing import Any


def build_index_body(index_name: str) -> dict[str, Any]:
    """返回可直接展开给 `client.indices.create(index=..., **body)` 的结构。"""
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,  # 开发栈单节点；生产按集群规模调
            "refresh_interval": "1s",
            "analysis": {
                "analyzer": {
                    # 中文场景：standard 分词 + 小写，先保证可用；后续可换 IK 分词器
                    "default_cn": {"type": "standard", "stopwords": "_none_"},
                }
            },
        },
        "mappings": {
            "dynamic": "strict",  # 拒绝未声明字段，避免 mapping 漂移
            "properties": {
                "text": {"type": "text", "analyzer": "default_cn"},
                "title": {
                    "type": "text",
                    "analyzer": "default_cn",
                    "fields": {"raw": {"type": "keyword", "ignore_above": 256}},
                },
                "doc_id": {"type": "keyword"},
                "kb_id": {"type": "keyword"},
                "tags": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "content_len": {"type": "integer"},
                "updated_at": {"type": "date"},
            },
        },
    }


def build_bulk_action(index_name: str, chunk_id: str, document: dict[str, Any]) -> dict[str, Any]:
    """单条 index 动作，供 `client.bulk(operations=...)` 使用。

    `_id` 固定为 chunk_id —— 重跑摄入时天然幂等覆盖，不需先删。
    """
    return {
        "_op_type": "index",
        "_index": index_name,
        "_id": chunk_id,
        "_source": document,
    }


def delete_by_doc_id_body(kb_id: str, doc_id: str) -> dict[str, Any]:
    """按 doc_id 清理残留——摄入失败重跑前的可重入清理（裁决 #3）。"""
    return {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"kb_id": kb_id}},
                    {"term": {"doc_id": doc_id}},
                ]
            }
        }
    }
