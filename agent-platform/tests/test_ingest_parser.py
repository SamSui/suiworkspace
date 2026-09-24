"""4.1 文档解析器单测（纯本地，不依赖外部服务）。

覆盖验收口径：
- pdf / docx / md / txt 四格式样例解析成功；
- ≤200MB 上限：超过 `INGEST_MAX_DOCUMENT_MB` 的样本被 `validate_document` 拒绝；
- 非白名单扩展名拒绝；
- 损坏 / 无文本内容 → ParserError（worker 据此置 FAILED）。

解析库来自 `pyproject.toml` 的 `parser` extra（pypdf / python-docx）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingest.parser import ParserError, extract_text, validate_document


@pytest.fixture()
def tmp_docs(tmp_path: Path) -> Path:
    """在临时目录生成四格式样例文件，返回目录。"""
    (tmp_path / "sample.md").write_text("# 标题\n\n这是报销流程说明。\n", encoding="utf-8")
    (tmp_path / "sample.txt").write_text("纯文本内容：提交申请->走审批。\n", encoding="utf-8")

    import docx

    d = docx.Document()
    d.add_paragraph("DOCX 段落正文：报销需提前申请。")
    d.save(str(tmp_path / "sample.docx"))

    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=595, height=842)  # A4
    # 注册标准 Helvetica，使内容流 BT/ET Tj 可被 pypdf 稳定抽取
    font = DictionaryObject(
        {NameObject("/BaseFont"): NameObject("/Helvetica"),
         NameObject("/Subtype"): NameObject("/Type1"),
         NameObject("/Type"): NameObject("/Font")}
    )
    fonts = DictionaryObject({NameObject("/F1"): font})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 72 770 Td (PDF procurement process) Tj ET")
    page[NameObject("/Contents")] = content
    with open(tmp_path / "sample.pdf", "wb") as f:
        writer.write(f)
    return tmp_path


@pytest.mark.parametrize("fname", ["sample.pdf", "sample.docx", "sample.md", "sample.txt"])
def test_extract_text_ok(tmp_docs: Path, fname: str) -> None:
    """四格式均能「解析成功」返回纯文本。"""
    text = extract_text(tmp_docs / fname)
    assert isinstance(text, str), f"{fname} 应返回 str"
    if fname == "sample.pdf":
        assert "procurement" in text, f"PDF 应抽取注入文本，实得 {text!r}"
        return
    assert text.strip(), f"{fname} 应抽取到非空文本"
    assert "报销" in text or "流程" in text or "提交" in text or "标题" in text


def test_validate_document_accepts_all(tmp_docs: Path) -> None:
    for fname in ("sample.pdf", "sample.docx", "sample.md", "sample.txt"):
        validate_document(tmp_docs / fname)  # 不应抛


def test_unsupported_extension_rejected(tmp_docs: Path) -> None:
    bad = tmp_docs / "evil.exe"
    bad.write_bytes(b"not a doc")
    with pytest.raises(Exception) as ei:
        validate_document(bad)
    assert "不支持" in str(ei.value) or "invalid_argument" in getattr(ei.value, "code", "")


def test_overlimit_rejected(tmp_docs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """>200MB（通过测前把受限上限压到极小）→ 拒绝。"""
    from core.config import get_settings
    from core.exceptions import ValidationError

    # 把上限压到 0，制造超限样本而不需真写 200MB
    monkeypatch.setenv("INGEST_MAX_DOCUMENT_MB", "0")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            validate_document(tmp_docs / "sample.txt")
    finally:
        get_settings.cache_clear()


def test_empty_pdf_raises_parserror(tmp_path: Path) -> None:
    """空 PDF（无文本页）→ ParserError，worker 置 FAILED 的依据。"""
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    p = tmp_path / "empty.pdf"
    with open(p, "wb") as f:
        writer.write(f)
    with pytest.raises(ParserError):
        extract_text(p)