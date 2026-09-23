"""文档上传与摄入任务路由（增量 2 交付；链路实现在增量 4）。

上传只做：校验（类型白名单 + 大小上限 ≤200MB）→ 落盘/对象存储 → 写
`document(status=0)` → 投递 Redis 队列。解析/切分/Embedding 一律在 worker 内，
绝不进请求路径（设计文档 §8）。
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from core.exceptions import NotImplementedYet

router = APIRouter(prefix="/v1/doc", tags=["document"])

_INCREMENT = "增量 2（api 网关）"


class DocumentOut(BaseModel):
    id: int
    kb_id: int
    file_name: str
    status: int  # DocumentStatus: 0未处理 1处理中 2完成 3失败
    chunk_count: int = 0


@router.post("", response_model=DocumentOut, status_code=202)
async def upload_document(
    kb_id: int = Form(...),
    file: UploadFile = File(...),
) -> None:
    """上传文档并入队摄入。返回 202 与 document 记录（status=0）。"""
    raise NotImplementedYet(_INCREMENT)


@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(doc_id: int) -> None:
    raise NotImplementedYet(_INCREMENT)


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: int) -> None:
    """删除文档：级联清理 ES / Milvus 两侧数据（按 doc_id）。"""
    raise NotImplementedYet(_INCREMENT)
