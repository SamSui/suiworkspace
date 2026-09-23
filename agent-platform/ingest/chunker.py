"""文本切分（增量 4 交付）。

裁决 #5 定下的参数：~250 token / 片，overlap 100。
本增量只固定参数入口，切分策略（是否走语义边界）待增量 4 定稿。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.exceptions import NotImplementedYet

_INCREMENT = "增量 4（ingest 摄入）"


@dataclass(slots=True)
class Chunk:
    index: int
    text: str
    token_count: int


def split_text(
    text: str,
    *,
    chunk_tokens: int = 250,
    overlap: int = 100,
    title: str | None = None,
) -> list[Chunk]:
    """按 ~chunk_tokens 切分，相邻片重叠 overlap。

    待定：tokenizer 选型（tiktoken vs 模型自带）会直接影响切片边界与
    `message.content` 摘要长度，需在增量 4 与评估基准一起确定。
    """
    raise NotImplementedYet(_INCREMENT, "切分策略待增量 4 定稿")
