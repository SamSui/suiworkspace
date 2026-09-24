"""文本切分（增量 4.2 落地）。

裁决 #5 定下的参数：`~INGEST_CHUNK_TOKENS`（默认 250）token / 片，
overlap `INGEST_CHUNK_OVERLAP`（默认 100）。

算法（在 token 边界上滑窗，**无损重建原文**，可复现）：
1. `tokenizer.encode(text)` → 无损 token 序列（含保留的空格 token）；
2. 取「内容 token」下标序列（剔除纯空白 token）；
3. 以内容 token 数计步长 `step = chunk_tokens - overlap`，枚举内容窗口起点；
4. 每片内容窗口 `[s, s+chunk_tokens)` 映射回**原始 token 下标区间**切片，`"".join` 还原
   ——原文既不丢词也不脆断；相邻片按内容 token 重叠 overlap；
5. 尾部碎片（内容 token < step）并入前一片，避免空/极小片。

边界：
- 短文本（内容 token ≤ chunk_tokens）→ 恰 1 片；
- 纯空白 / 空文本 → []；
- overlap 拉满 == chunk_tokens（step==0）→ 整篇一片；
- 单 token 超长（无空格连续串）已被估算器切成 ≤4 字符粒度，天然可再分片。
"""

from __future__ import annotations

from dataclasses import dataclass

from ingest.tokenizer import encode

_DEFAULT_CHUNK_TOKENS = 250
_DEFAULT_OVERLAP = 100


@dataclass(slots=True)
class Chunk:
    index: int
    text: str
    token_count: int


def split_text(
    text: str,
    *,
    chunk_tokens: int = _DEFAULT_CHUNK_TOKENS,
    overlap: int = _DEFAULT_OVERLAP,
    title: str | None = None,
) -> list[Chunk]:
    """按 ~chunk_tokens 切分，相邻片重叠 overlap。`title` 预留不带元数据。"""
    del title  # 预留参数：当前不参与切分边界
    if not text:
        return []
    if chunk_tokens <= 0 or chunk_tokens < overlap:
        raise ValueError("chunk_tokens 必须为正且 >= overlap")

    toks = encode(text)
    content = [i for i, t in enumerate(toks) if not t.isspace()]
    c = len(content)
    if c == 0:
        return []

    step = chunk_tokens - overlap
    if step <= 0:
        return [Chunk(index=0, text="".join(toks), token_count=c)]

    # 内容窗口起点（content-token 空间）
    starts: list[int] = []
    s = 0
    while s < c:
        starts.append(s)
        s += step

    # 内容范围 [s, min(s+chunk_tokens, c))
    ranges: list[tuple[int, int]] = []
    for s in starts:
        e = min(s + chunk_tokens, c)
        ranges.append((s, e))

    # 尾部碎片（内容 token < step 且非首片）并入前一片末端（消除空小片）
    ranges = _fold_tail(ranges, step, c)

    chunks: list[Chunk] = []
    for r_s, r_e in ranges:
        if r_e <= r_s:
            continue
        tok_start = content[r_s]
        tok_end = content[r_e - 1] + 1
        chunk_toks = toks[tok_start:tok_end]
        chunks.append(
            Chunk(
                index=len(chunks),
                text="".join(chunk_toks),
                token_count=r_e - r_s,
            )
        )
    return chunks


def _fold_tail(ranges: list[tuple[int, int]], step: int, c: int) -> list[tuple[int, int]]:
    """把内容 token 数 < step 的尾片并入前一片末端（消除空小片，可重入）。"""
    if len(ranges) <= 1:
        return ranges
    s_last, e_last = ranges[-1]
    if e_last - s_last < step:
        # 并入前一片：把前一片的右边界扩到尾片右边界
        s_prev, _ = ranges[-2]
        merged: list[tuple[int, int]] = ranges[:-2] + [(s_prev, e_last)]
        return merged
    return ranges


__all__ = ["Chunk", "split_text"]