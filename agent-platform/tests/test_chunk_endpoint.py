"""SUIG-39 `GET /v1/doc/{id}/chunk/{chunk_id}` 端点单测。

不依赖 live MySQL：把 `_load_doc_for_owner`（文档归属校验）与 `container.es`
（ES 取正文）两层都打桩，聚焦验证 get_chunk 的归属/存在性 404 语义与 200 结构。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from api.routers import document
from core.exceptions import NotFound


class _Doc:
    def __init__(self, doc_id: int, kb_id: int) -> None:
        self.id = doc_id
        self.kb_id = kb_id


class _ES:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch_chunks(self, ids: list[str]) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]


class _Container:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.es = _ES(rows)


class _User:
    pass


def _call(
    *,
    doc_id: int,
    chunk_id: str,
    rows: list[dict[str, Any]],
    owner_id: int = 1,
    owner_kb: int = 8,
    owner_found: bool = True,
):
    async def _load(container, did: int, user):
        if not owner_found:
            raise NotFound("文档不存在")
        return _Doc(owner_id, owner_kb)

    # 打桩文档归属校验，绕过真实 MySQL
    document._load_doc_for_owner = _load  # type: ignore[assignment]
    conn = _Container(rows)
    try:
        return asyncio.run(
            document.get_chunk(doc_id, chunk_id, user=_User(), container=conn)
        )
    except NotFound as nf:
        return nf
    finally:
        # 还原，避免污染其它测试
        import api.routers.document as m

        del m._load_doc_for_owner  # type: ignore[attr-defined]


def test_get_chunk_happy_path() -> None:
    out = _call(
        doc_id=1,
        chunk_id="ck-1",
        rows=[{"chunk_id": "ck-1", "doc_id": 1, "kb_id": 8, "text": "报销走线上审批"}],
    )
    assert not isinstance(out, Exception)
    assert out.chunk_id == "ck-1"
    assert out.doc_id == 1
    assert out.kb_id == 8
    assert out.text == "报销走线上审批"


def test_get_chunk_missing_row_404() -> None:
    """ES 无该 chunk → 404（不泄漏来源是否存在）。"""
    result = _call(doc_id=1, chunk_id="ck-missing", rows=[])
    assert isinstance(result, NotFound)
    assert result.status_code == 404


def test_get_chunk_mismatch_doc_404() -> None:
    """chunk 的 doc_id 与 URL 不一致 → 404（防跨文档越权）。"""
    result = _call(doc_id=1, chunk_id="ck-1", rows=[{"chunk_id": "ck-1", "doc_id": 99, "kb_id": 8}])
    assert isinstance(result, NotFound)


def test_get_chunk_mismatch_kb_404() -> None:
    """chunk 的 kb_id 与文档所属 kb 不一致 → 404（防跨库越权）。"""
    result = _call(doc_id=1, chunk_id="ck-1", rows=[{"chunk_id": "ck-1", "doc_id": 1, "kb_id": 9}])
    assert isinstance(result, NotFound)


def test_get_chunk_owner_doc_missing_404() -> None:
    """文档本身不存在 → 先 404（归属防线在最前）。"""
    result = _call(doc_id=1, chunk_id="ck-1", rows=[{"chunk_id": "ck-1", "doc_id": 1, "kb_id": 8}],
                   owner_found=False)
    assert isinstance(result, NotFound)