"""异步任务查询路由（增量 2.5）。

摄入任务是长耗时后台作业（增量 4 落地实际解析/双写），前端通过 `GET /v1/task/{id}`
轮询状态，而不是在上传请求里等结果。

**任务即文档**：本中台一个摄入任务对应一篇 `document`，`task_id` 即 `document.id`，
状态机以 `document.status` 为唯一真相源（裁决 #3，0未处理 1处理中 2完成 3失败）。
这里把库里的 status 直接透出，保证与 `document.status` 严格一致。

权限：按任务对应的 kb 反查归属，越权与不存在统一 404（不泄漏存在性）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from api.deps import current_user, get_container, require_kb_access
from core.exceptions import NotFound
from core.logging import get_logger
from db.models import Document

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/task", tags=["task"])


class TaskOut(BaseModel):
    task_id: str
    doc_id: int
    status: int  # 与 document.status 同构
    progress: float = 0.0
    error_msg: str | None = None


# 状态 → 轮询进度（近似；真实进度由 worker 在增量 4 以 chunk 粒度回写）
_PROGRESS = {0: 0.0, 1: 0.5, 2: 1.0, 3: 1.0}


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: str,
    user=Depends(current_user),
    container=Depends(get_container),
) -> TaskOut:
    """查询摄入任务状态（与对应 `document.status` 严格一致）。"""
    try:
        doc_id = int(task_id)
    except ValueError:
        raise NotFound("任务不存在") from None

    async with container.mysql.session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one_or_none()
        if doc is None:
            raise NotFound("任务不存在")
        # 权限：文档归属校验，越权/不存在统一 404
        await require_kb_access(doc.kb_id, user, session)
        status = doc.status
        error_msg = doc.error_msg

    return TaskOut(
        task_id=task_id,
        doc_id=doc_id,
        status=status,
        progress=_PROGRESS.get(status, 0.0),
        error_msg=error_msg,
    )