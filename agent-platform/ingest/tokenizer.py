"""文本 tokenizer（增量 4.2 tokenizer 选型落地）。

背景：增量 1 登记"tokenizer 选型"为开放决策，本增量 4.2 定稿，并连同
`message.content` 摘要长度约定一起落到交付文档。

**选型结论**：运行时默认走**确定性离线估算器**（本模块），不引入 tiktoken /
sentence-transformers 等需联网下载词汇表的第三方契约——理由：

1. 本环境无真实模型下载授权（ingest run 的既定约束）：嵌入走 echo 桩、LLM 走
   echo provider，tokenizer 若强依赖某模型词汇表会在离线环境直接不可用，
   违背"未验证契约不引入"的约束。
2. 确定性 + 零下载：同输入恒同切分，可离线复现、可单测精确断言，与
   `MILVUS_DIM`/embedding 桩一脉相承。
3. **可插拔**：本模块只暴露 `count / encode` 纯函数接口，真实接入（tiktoken
   `cl100k_base` / BGE 自带 tokenizer）在 4/5 联动时替换 `_IMPL` 即可。

估算规则（语言无关、确定性）：
- CJK（中日韩）单字符 ≈ 1 token；
- 连续 ASCII/数字按 4 字符 ≈ 1 token 折算（不足 4 仍是 1）；
- 空白（空格/换行/制表）token 权重为 0，不计入计数，但**保留在 token 序列里**，
  保证 `"".join(encode(text)) == text`（无损重建，切分后原文不丢）。

**约定（本次一并定稿）**：
- `document.chunk_count` 与 ES `chunk_index` 以本估算器产生为准；
- 切片粒度 `~INGEST_CHUNK_TOKENS`（默认 250）；
- `message.content` 摘要长度：正文摘要建议收敛到 ≤128 token（配合 chunk 250 的一半
  余量），MySQL 只承载引用，正文在 ES（设计文档 §5.3）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Tokenizer(Protocol):
    """tokenizer 接口：真实实现（tiktoken/BGE）按此协议替换 `_IMPL`。"""

    def encode(self, text: str) -> list[str]: ...


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3400 <= o <= 0x9FFF
        or 0xF900 <= o <= 0xFAFF
        or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF
    )


@dataclass(frozen=True, slots=True)
class EstimatorTokenizer:
    """确定性离线估算器。`encode` 无损：`"".join(encode(text)) == text`。"""

    scale: int = 4  # ASCII 每 scale 字符 ≈ 1 token

    def encode(self, text: str) -> list[str]:
        return _tokenize_estimate(text, self.scale)


def _tokenize_estimate(text: str, scale: int) -> list[str]:
    """切 token 序列：CJK 单字符一个 token，ASCII run 按 scale 分块，空白独立保留。"""
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            tokens.append(text[i:j])
            i = j
            continue
        if _is_cjk(ch):
            tokens.append(ch)
            i += 1
            continue
        j = i
        while j < n and not text[j].isspace() and not _is_cjk(text[j]):
            j += 1
        run = text[i:j]
        for k in range(0, len(run), scale):
            tokens.append(run[k : k + scale])
        i = j
    return tokens


_IMPL: Tokenizer = EstimatorTokenizer()


def encode(text: str) -> list[str]:
    """返回无损 token 序列：`"".join(encode(text)) == text`。"""
    return _IMPL.encode(text)


def count_tokens(text: str) -> int:
    """估算 token 数（确定性；空白不计）。"""
    return sum(1 for tok in encode(text) if not tok.isspace())


__all__ = ["Tokenizer", "EstimatorTokenizer", "encode", "count_tokens"]