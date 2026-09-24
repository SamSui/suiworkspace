"""2.4 上传校验纯单测：类型白名单 + 大小上限 + sha256（不依赖数据库）。

直接测 `api.routers.document._drain_and_validate` 这个纯校验函数，用
`UploadFile + BytesIO` 构造输入，避免一站式集成测试对存储的依赖。
"""

from __future__ import annotations

import hashlib
import io

import pytest
from fastapi import UploadFile

from api.routers.document import _drain_and_validate
from core.exceptions import ValidationError

_ALLOWED = {".pdf", ".docx", ".md", ".txt"}
_MB = 1024 * 1024


def _upload(content: bytes, filename: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


async def test_valid_file_returns_hash_and_size() -> None:
    content = b"# H1\nbody"
    digest, size, spool = await _drain_and_validate(
        _upload(content, "notes.md"), _ALLOWED, max_bytes=1 * _MB
    )
    try:
        assert size == len(content)
        assert digest == hashlib.sha256(content).hexdigest()
        # 校验通过后 spool 定位回开头，便于落盘读入
        assert spool.read() == content
    finally:
        spool.close()


async def test_disallowed_extension_rejected() -> None:
    with pytest.raises(ValidationError):
        await _drain_and_validate(_upload(b"MZz", "evil.exe"), _ALLOWED, max_bytes=1 * _MB)


async def test_no_extension_rejected() -> None:
    with pytest.raises(ValidationError):
        await _drain_and_validate(_upload(b"data", "README"), _ALLOWED, max_bytes=1 * _MB)


async def test_oversize_rejected() -> None:
    # 1KB 上限，喂 4KB —— 提前中止，不读完整份
    with pytest.raises(ValidationError):
        await _drain_and_validate(_upload(b"x" * 4096, "big.txt"), _ALLOWED, max_bytes=1024)


async def test_empty_file_rejected() -> None:
    with pytest.raises(ValidationError):
        await _drain_and_validate(_upload(b"", "empty.txt"), _ALLOWED, max_bytes=1 * _MB)


async def test_extension_case_insensitive() -> None:
    *_, spool = await _drain_and_validate(_upload(b"x", "A.TXT"), _ALLOWED, max_bytes=1 * _MB)
    try:
        pass
    finally:
        spool.close()