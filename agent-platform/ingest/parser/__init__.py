"""文档解析（增量 4 交付）。

格式白名单与单文档上限来自裁决 #5：≤200MB，.pdf/.docx/.md/.txt。
"""

from core.exceptions import NotImplementedYet

_INCREMENT = "增量 4（ingest 摄入）"


def extract_text(path: str) -> str:
    """按扩展名分发解析器，返回纯文本。"""
    raise NotImplementedYet(_INCREMENT, "文档解析器尚未接入")
