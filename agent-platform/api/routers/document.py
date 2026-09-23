"""文档上传与摄入任务路由（增量 2 交付；实际解析链路在增量 4）。

上传链路只做：
    鉴权(401) → kb 归属校验(404) → 类型/大小白名单校验(4xx) → sha256 去重 →
    落盘(本地文件后端) → 写 `document(status=0)` → 入队 arq → 返回 202。
请求内**不做任何解析/切分/Embedding**，全部交给 `ingest.worker`（增量 4）。

权限：一律走 `require_kb_access`（查询路径强制，裁决 #4）。文档归属其 kb，
任何 doc 读/删路径都先反查 kb 归属——越权与不存在统一 404（不泄漏存在性）。
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select

from api.deps import current_user, get_container, require_kb_access
from core.exceptions import NotFound, ValidationError
from core.logging import get_logger
from core.storage import StorageContainer
from core.storage.local import delete_upload, save_upload
from db.models import Document, User

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/doc", tags=["document"])

# 分块读入字节数（1MB）；既用于流式 hash，也用于大小校验的提前中止。
_CHUNK = 1024 * 1024
# 落盘用 SpooledTemporaryFile 超过该阈值自动溢出到磁盘，避免大文件驻留内存。
_SPOOL_MAX = 8 * 1024 * 1024


class DocumentOut(BaseModel):
    id: int
    kb_id: int
    file_name: str
    status: int  # DocumentStatus: 0未处理 1处理中 2完成 3失败
    chunk_count: int = 0


def _doc_to_out(doc: Document) -> DocumentOut:
    return DocumentOut(
        id=int(doc.id),
        kb_id=int(doc.kb_id),
        file_name=doc.file_name,
        status=doc.status,
        chunk_count=doc.chunk_count,
    )


async def _drain_and_validate(
    file: UploadFile, extensions: set[str], max_bytes: int
) -> tuple[str, int, tempfile.SpooledTemporaryFile]:
    """流式读入：类型白名单 + 大小上限校验，算出 sha256。

    返回 `(sha256, 字节数, 临时文件)`。临时文件用 `SpooledTemporaryFile`，
    大文件自动溢出磁盘；调用方必须在落盘后 `close()` 清理。
    任一校验失败抛 `ValidationError`（4xx）。
    """
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in extensions:
        raise ValidationError(f"不支持的文件类型：{suffix or '(无扩展名)'}")

    spool: tempfile.SpooledTemporaryFile = tempfile.SpooledTemporaryFile(
        max_size=_SPOOL_MAX
    )
    digest = hashlib.sha256()
    size = 0
    try:
        while chunk := await file.read(_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise ValidationError(
                    f"文件超过大小上限 {max_bytes // (1024 * 1024)}MB"
                )
            digest.update(chunk)
            spool.write(chunk)

        if size == 0:
            raise ValidationError("上传文件为空")

        spool.seek(0)
        return digest.hexdigest(), size, spool
    except Exception:
        spool.close()
        raise


async def _load_doc_for_owner(
    container: StorageContainer, doc_id: int, user: User
) -> Document:
    """按 id 取文档并强制 kb 归属校验（越权/不存在统一 404）。"""
    async with container.mysql.session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one_or_none()
        if doc is None:
            raise NotFound("文档不存在")
        await require_kb_access(doc.kb_id, user, session)
        return doc


@router.post("", response_model=DocumentOut, status_code=202)
async def upload_document(
    kb_id: int = Form(...),
    file: UploadFile = File(...),
    user=Depends(current_user),
    container=Depends(get_container),
) -> DocumentOut:
    """上传文档并入队摄入。返回 202 与 `document(status=0)` 记录。

    类型/大小白名单、kb 归属校验均在此完成；请求内不做解析。
    """
    settings = container.settings

    # 1) kb 归属校验（查询路径强制，404）
    async with container.mysql.session() as session:
        await require_kb_access(kb_id, user, session)

    # 2) 类型/大小校验 + 流式 hash（大文件提前中止）
    digest, _size, spool = await _drain_and_validate(
        file, settings.ingest.allowed_ext_set, settings.ingest.max_document_mb * 1024 * 1024
    )
    try:
        # 3) 落盘本地（路径只用服务端生成的 kb_id + hash，防路径穿越）
        await save_upload(settings, spool, kb_id, digest)

        # 4) 写 document(status=0)；同库同 hash 去重（uk_kb_hash）
        async with container.mysql.session() as session:
            existing = (
                await session.execute(
                    select(Document).where(
                        Document.kb_id == kb_id, Document.file_hash == digest
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                doc = existing
            else:
                doc = Document(
                    kb_id=kb_id, file_name=file.filename or "", file_hash=digest, status=0
                )
                session.add(doc)

            # server_default(status=0/chunk_count=0) 在 flush 后于库侧生成
            await session.flush()
            await session.refresh(doc)
            doc_out = _doc_to_out(doc)

        # 5) 入队（尽力而为；队列不可用记日志，不阻塞 202 返回）
        from ingest.enqueue import enqueue_document

        await enqueue_document(container, int(doc_out.id))

        return doc_out
    finally:
        spool.close()


@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(
    doc_id: int,
    user=Depends(current_user),
    container=Depends(get_container),
) -> DocumentOut:
    doc = await _load_doc_for_owner(container, doc_id, user)
    return _doc_to_out(doc)


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: int,
    user=Depends(current_user),
    container=Depends(get_container),
) -> None:
    """删除文档：清理库记录 + 落盘文件。

    ES/Milvus 两侧残留由 ingest worker 于任务重跑前清理（裁决 #3），
    网关路径不直接访问向量/倒排库。
    """
    doc = await _load_doc_for_owner(container, doc_id, user)
    kb_id, file_hash = int(doc.kb_id), doc.file_hash
    async with container.mysql.session() as session:
        await session.execute(delete(Document).where(Document.id == doc_id))
    await asyncio.to_thread(delete_upload, container.settings, kb_id, file_hash)
    return None