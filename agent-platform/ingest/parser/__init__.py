"""文档解析（增量 4.1 落地）。

格式白名单与单文档上限来自裁决 #5：≤200MB，`.pdf/.docx/.md/.txt`。

职责：
- `get_extension` / `validate_document`：类型白名单 + ≤`INGEST_MAX_DOCUMENT_MB`（默认 200）校验，
  超限或非白名单直接抛 `ValidationError`（worker 捕获后置 FAILED）。
- `extract_text(path)`：按扩展名分发到 pdf / docx / md / txt 子解析器，返回纯文本。

解析库：pypdf / python-docx 来自 `pyproject.toml` 的 `parser` extra（预声明依赖集，合规）。
对未知异常抛出可诊断的 `ParserError` → worker 置 FAILED 并记录 error_msg。
"""

from __future__ import annotations

from pathlib import Path

import pypdf

from core.config import get_settings
from core.exceptions import ValidationError
from core.logging import get_logger

logger = get_logger(__name__)

_MB = 1024 * 1024


class ParserError(Exception):
    """解析失败（内容损坏 / 无文本等），由 worker 捕获置 FAILED。"""


def _limit_bytes() -> int:
    return get_settings().ingest.max_document_mb * _MB


def _allowed_ext() -> set[str]:
    return get_settings().ingest.allowed_ext_set


def validate_document(path: str | Path) -> None:
    """校验格式白名单 + ≤200MB。超限/非白名单 → ValidationError（请求语义，502/422）。"""
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in _allowed_ext():
        allowed = sorted(_allowed_ext())
        raise ValidationError(
            f"不支持的文件类型 '{ext or '(无扩展名)'}'", detail={"allowed": allowed}
        )
    size = p.stat().st_size
    if size > _limit_bytes():
        raise ValidationError(
            f"文件超过上限 {get_settings().ingest.max_document_mb}MB",
            detail={"size_bytes": size, "limit_bytes": _limit_bytes()},
        )


def extract_text(path: str | Path) -> str:
    """按扩展名分发解析器，返回纯文本。调用方需先过 `validate_document`。"""
    path = str(path)
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(path)
    if ext == ".docx":
        return _parse_docx(path)
    if ext == ".md":
        return _parse_md(path)
    if ext == ".txt":
        return _parse_txt(path)
    raise ValidationError(f"不支持的文件类型 '{ext or '(无扩展名)'}'")


def _parse_pdf(path: str) -> str:
    """pypdf 逐页抽取文本。空文档/无可抽取文本 → ParserError。"""
    try:
        reader = pypdf.PdfReader(path)
        parts = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 — 外部解析库异常需收敛为可诊断错误
        raise ParserError(f"PDF 解析失败: {exc}") from exc
    text = "\n".join(parts).strip()
    if not text:
        raise ParserError("PDF 未抽取到文本（可能是扫描件/空页）")
    return text


def _parse_docx(path: str) -> str:
    """python-docx 抽取段落正文（含表格单元格）。"""
    try:
        import docx

        d = docx.Document(path)
        parts: list[str] = []
        for para in d.paragraphs:
            if para.text:
                parts.append(para.text)
        for table in d.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text:
                        parts.append(cell.text)
    except Exception as exc:  # noqa: BLE001
        raise ParserError(f"DOCX 解析失败: {exc}") from exc
    text = "\n".join(parts).strip()
    if not text:
        raise ParserError("DOCX 未抽取到文本（可能是空文档）")
    return text


def _parse_md(path: str) -> str:
    """Markdown 直接读文本（不渲染语法，原文即检索半成品）。"""
    return _read_text_file(path)


def _parse_txt(path: str) -> str:
    """纯文本，容错多编码（utf-8 → gbk → latin-1 兜底）。"""
    return _read_text_file(path)


def _read_text_file(path: str) -> str:
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            with open(path, encoding=encoding) as f:
                text = f.read()
            return text.strip()
        except UnicodeDecodeError:
            continue
    raise ParserError(f"无法用 utf-8/gbk/latin-1 解码文本文件: {path}")


__all__ = ["extract_text", "validate_document", "ParserError"]