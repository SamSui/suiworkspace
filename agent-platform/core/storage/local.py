"""本地文件落盘（增量 2.4 上传路径的存储后端）。

增量 2.4 只要求「校验 → 落盘 → 写 `document(status=0)` → 入队」。此处负责把
上传字节流安全落盘：路径只用服务端生成字段（`kb_id` / `file_hash`）分桶，
**绝不使用客户端文件名拼路径**——防路径穿越（裁决：服务器生成的才是可信输入）。

对象存储（MinIO）在增量 5 接入时，仅需替换本模块的实现，接口保持 `save_upload` /
  `path_for` 两个函数即可。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from core.config import Settings


def upload_root(settings: Settings) -> Path:
    """落盘根目录：配置里 `INGEST_UPLOAD_DIR` 相对项目根解析为绝对路径。"""
    configured = settings.ingest.upload_dir
    root = Path(configured)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / configured
    root.mkdir(parents=True, exist_ok=True)
    return root


def object_path(settings: Settings, kb_id: int, file_hash: str) -> Path:
    """按 kb_id + sha256 确定对象路径（不掺客户端文件名）。"""
    root = upload_root(settings)
    # 按 hash 前两位再分一层桶，避免单目录文件过多
    obj = root / str(kb_id) / file_hash[:2] / file_hash
    obj.parent.mkdir(parents=True, exist_ok=True)
    return obj


async def save_upload(
    settings: Settings,
    source: tempfile.SpooledTemporaryFile,
    kb_id: int,
    file_hash: str,
) -> Path:
    """把已校验、已算好 hash 的字节流落盘。

    *同步* 文件写不占用事件循环太多——走 `asyncio.to_thread` 委派线程池，
      避免大文件在网关事件循环里阻塞其他请求。
    """
    import asyncio

    def _write() -> Path:
        target = object_path(settings, kb_id, file_hash)
        with open(target, "wb") as dst:
            # 逐块拷贝，不让整份文件驻留内存
            while chunk := source.read(64 * 1024):
                dst.write(chunk)
        return target

    return await asyncio.to_thread(_write)


def delete_upload(settings: Settings, kb_id: int, file_hash: str) -> None:
    """删除已落盘文件（`DELETE /v1/doc` 级联清理）。文件不存在则静默跳过。"""
    obj = object_path(settings, kb_id, file_hash)
    if obj.exists():
        os.remove(obj)