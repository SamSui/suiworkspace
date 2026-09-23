"""异步任务查询路由（增量 2 交付）。

摄入任务是长耗时后台作业，前端通过此接口轮询（或后续接 WS 订阅）状态，
而不是在上传请求里等结果。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from core.exceptions import NotImplementedYet

router = APIRouter(prefix="/v1/task", tags=["task"])

_INCREMENT = "增量 2（api 网关）"


class TaskOut(BaseModel):
    task_id: str
    doc_id: int
    status: int  # 与 document.status 同构
    progress: float = 0.0
    error_msg: str | None = None


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(_task_id: str) -> None:
    raise NotImplementedYet(_INCREMENT)
