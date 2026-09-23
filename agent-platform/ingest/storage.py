"""双写落库（增量 4 交付）。

单 worker 内按 chunk 串行写 Milvus + ES（裁决 #3）：
- Milvus：只写向量 + 主键 + 标量字段；
- ES：写原文切片，`_id` 固定为 chunk_id → 重跑天然幂等覆盖。
"""

from __future__ import annotations

from typing import Any

from core.exceptions import NotImplementedYet

_INCREMENT = "增量 4（ingest 摄入）"


async def write_chunks(
    *,
    kb_id: str,
    doc_id: str,
    chunks: list[Any],
    vectors: list[list[float]],
    title: str | None = None,
) -> int:
    """串行双写两侧，返回写入切片数。任一侧失败抛异常 → 由状态机置 FAILED 并退避重跑。"""
    raise NotImplementedYet(_INCREMENT, "双写落库尚未实现")
