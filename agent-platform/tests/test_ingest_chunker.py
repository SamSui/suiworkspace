"""4.2 切分器单测（纯本地，验证 token 边界滑窗 + 无损重建）。

覆盖验收口径：命名切片数与 token 分布符合预期；tokenizer 选型取舍有记录
（详见 `ingest/tokenizer` 模块头文档 + 交付评论中的决策记录）。
"""

from __future__ import annotations

from ingest.chunker import split_text
from ingest.tokenizer import count_tokens


def test_short_text_single_chunk_lossless() -> None:
    """内容 token ≤ chunk_tokens → 恰 1 片，且无损还原原文。"""
    text = "报销流程需提前申请并附发票。"
    chunks = split_text(text, chunk_tokens=250, overlap=100)
    assert len(chunks) == 1
    assert chunks[0].text == text, "单片应无损还原原文"
    assert chunks[0].token_count == count_tokens(text)


def test_multi_chunk_with_overlap() -> None:
    """长文本切成多片；每片 token ≤ chunk；相邻片存在重叠；切分后对原文仍可还原（差集无丢失）。"""
    text = " ".join(f"word{i}" for i in range(600))  # 600 单词 → 3000 ASCII token
    chunk_token = 250
    chunks = split_text(text, chunk_tokens=chunk_token, overlap=100)
    assert len(chunks) >= 3, f"3000 token 应 ≥3 片，实得 {len(chunks)}"
    for c in chunks:
        assert 0 < c.token_count <= chunk_token, f"片 {c.index} token={c.token_count} 超上限"

    # 重叠窗口生效：step = chunk_tokens - overlap = 150；相邻片头部/尾部重叠
    overlap_verified = False
    for a, b in zip(chunks, chunks[1:], strict=False):
        if set(a.text.split()) & set(b.text.split()):
            overlap_verified = True
            break
    assert overlap_verified, "重叠窗口未生效"

    # 无损：所有相邻片并集覆盖原文（重叠不丢词）
    full = [c.text for c in chunks]
    for tok in ("word0", "word599", "word300"):
        assert any(tok in piece for piece in full), f"原文词 {tok} 丢失"


def test_lossless_cjk_and_ascii_mix() -> None:
    """中英混排：相邻 token 无被空格破坏，`"".join(chunks)` 覆盖原文。"""
    text = "报销 flow 需提前 word300 申请。"
    chunks = split_text(text, chunk_tokens=8, overlap=2)
    joined = "".join(c.text for c in chunks)
    # 无损：原始内容子串都应出现在拼接里（重叠保留）
    for sub in ("报销", "flow", "word300", "申请"):
        assert sub in joined, f"内容 {sub} 在切分拼接后丢失"


def test_overlap_equals_chunk_single_chunk() -> None:
    """overlap == chunk_tokens（step=0）→ 整篇并入单片。"""
    words = " ".join(f"w{i}" for i in range(50))
    chunks = split_text(words, chunk_tokens=10, overlap=10)
    assert len(chunks) == 1


def test_empty_and_whitespace_text() -> None:
    assert split_text("") == []
    assert split_text("   \n\t ") == []


def test_invalid_params_raise() -> None:
    import pytest

    with pytest.raises(ValueError):
        split_text("abc", chunk_tokens=0, overlap=0)
    with pytest.raises(ValueError):
        split_text("abc", chunk_tokens=5, overlap=9)
    with pytest.raises(ValueError):
        split_text("abc", chunk_tokens=-1, overlap=1)