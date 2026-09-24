"""增量 4 ingestion 集成测试（真实 Redis/Milvus/ES/MySQL，六服务在跑）。

覆盖验收口径（4.4 双写+状态机补偿 / 4.5 worker 幂等 / 4.6 端到端）：
- 双写：worker 完成后 Milvus / ES 均可见切片，`document.status=DONE(2)`；
- 状态机补偿（裁决 #3 核心）：**分别注入 ES 侧失败、Milvus 侧失败** → status=FAILED(3)，
  随后重跑（清理残留）→ 完整 DONE，且不残留旧切片（双写卫生）；
- 幂等：同文档重复投递 → 重跑不产生重复切片（ES `_id`/Milvus 主键幂等）；
- 端到端：摄入完成后，用与检索侧同源 embedding 的 query 到 Milvus/ES 检索可命中。

任一存储不可达时整体 skip（与 `test_kb_integration` 同策略）。
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from db.models import Document, DocumentStatus, KnowledgeBase, User


@pytest.fixture()
async def container() -> None:
    """探测真实存储；不可达整体 skip，可达则共享容器。"""
    from core.config import get_settings
    from core.storage import StorageContainer

    c = StorageContainer(get_settings())
    try:
        await c.startup(bootstrap=True, fail_fast=True)
    except Exception:  # noqa: BLE001
        pytest.skip("真实存储不可用，跳过 ingest 集成测试")
    yield c
    await c.shutdown()


class DocContext:
    """一次摄入用例的上下文（user/kb/document + 落盘文件 + 清理句柄）。"""

    def __init__(self, container, data_dir: Path):
        self.container = container
        self.data_dir = data_dir
        self.user_id: int | None = None
        self.kb_id: int | None = None
        self.doc_id: int | None = None
        self.file_name = ""
        self.file_hash = ""

    @property
    def file_path(self) -> Path:
        return self.data_dir / str(self.kb_id) / str(self.doc_id) / self.file_name


async def _make_doc(
    container, data_dir: Path, *, content: str, file_name: str = "sample.txt"
) -> DocContext:
    ctx = DocContext(container, data_dir)
    ctx.file_name = file_name
    ctx.file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    async with container.mysql.session() as s:
        user = User(
            name=f"ingest_u_{uuid.uuid4().hex[:10]}", api_key=f"key_{uuid.uuid4().hex[:20]}"
        )
        s.add(user)
        await s.flush()
        kb = KnowledgeBase(name=f"kb_{uuid.uuid4().hex[:8]}", owner_id=user.id)
        s.add(kb)
        await s.flush()
        doc = Document(kb_id=kb.id, file_name=file_name, file_hash=ctx.file_hash)
        s.add(doc)
        await s.flush()
        ctx.user_id, ctx.kb_id, ctx.doc_id = user.id, kb.id, doc.id
        ctx.file_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.file_path.write_text(content, encoding="utf-8")
    return ctx


async def _cleanup_doc(ctx: DocContext) -> None:
    from sqlalchemy import delete

    from ingest.storage import cleanup

    try:
        await cleanup(ctx.container, kb_id=str(ctx.kb_id), doc_id=str(ctx.doc_id))
    except Exception:  # noqa: BLE001 — 清理是 best-effort
        pass
    async with ctx.container.mysql.session() as s:
        await s.execute(delete(Document).where(Document.id == ctx.doc_id))
        await s.execute(delete(KnowledgeBase).where(KnowledgeBase.id == ctx.kb_id))
        await s.execute(delete(User).where(User.id == ctx.user_id))


def _data_root(container) -> Path:
    from pathlib import Path as _P

    p = _P(container.settings.ingest.data_dir)
    return p if p.is_absolute() else (_P.cwd() / p)


@pytest.fixture()
async def live_doc(container, tmp_path: Path) -> DocContext:
    content = (
        "报销流程：员工提交报销单，注明用途与金额。"
        "审批链到部门经理，超过五千元需财务复审。文档编号 DOC2026-0001。"
        "提交后进入审批流，流程可跟踪。"
    )
    root = _data_root(container)
    ctx = await _make_doc(container, root, content=content)
    yield ctx
    await _cleanup_doc(ctx)


async def _run_ingest(ctx: DocContext) -> None:
    from ingest.worker import ingest_document

    await ingest_document({"container": ctx.container}, ctx.doc_id)


async def _load_state(ctx: DocContext) -> Document:
    from sqlalchemy import select

    async with ctx.container.mysql.session() as s:
        return (await s.execute(select(Document).where(Document.id == ctx.doc_id))).scalar_one()


async def _count_es(ctx: DocContext) -> int:
    resp = await ctx.container.es.client.count(
        index=ctx.container.settings.es.index,
        query={"term": {"doc_id": str(ctx.doc_id)}},
    )
    return int(resp["count"])


async def _count_milvus(ctx: DocContext) -> int:
    from ingest.embedding import embed_query

    vec = await embed_query("报销")
    hits = await ctx.container.milvus.search(
        vec, kb_id=str(ctx.kb_id), top_k=200, output_fields=["doc_id"]
    )
    return sum(1 for h in hits if str(h.get("doc_id")) == str(ctx.doc_id))


# --------------------------------------------------------------------------- #
# 4.4 双写 + 状态机补偿
# --------------------------------------------------------------------------- #


async def test_dual_write_reaches_both_sides(container, live_doc: DocContext) -> None:
    """worker 完成后：Milvus + ES 均可见切片，status=DONE(2)。"""
    await _run_ingest(live_doc)
    doc = await _load_state(live_doc)
    msg = f"status 应 DONE({DocumentStatus.DONE.value})，实得 {doc.status}"
    assert doc.status_enum == DocumentStatus.DONE, msg
    assert doc.chunk_count >= 1

    es_count = await _count_es(live_doc)
    assert es_count == doc.chunk_count, f"ES 切片 {es_count} != chunk_count {doc.chunk_count}"
    milvus_count = await _count_milvus(live_doc)
    mmsg = f"Milvus 切片 {milvus_count} != chunk_count {doc.chunk_count}"
    assert milvus_count == doc.chunk_count, mmsg


async def test_4d_fault_es_fail_then_rerun(container, live_doc: DocContext, monkeypatch) -> None:
    """注错：ES 侧写失败 → status=FAILED(3)；重跑（清残留）→ DONE，不残留旧切片。"""
    from core.exceptions import UpstreamError
    from ingest.worker import ingest_document

    async def _fail_es(*a, **k):
        raise RuntimeError("injected es failure")

    monkeypatch.setattr("core.storage.es.ESStore.bulk_index", _fail_es)
    with pytest.raises(UpstreamError):
        await ingest_document({"container": live_doc.container}, live_doc.doc_id)
    doc = await _load_state(live_doc)
    assert doc.status_enum == DocumentStatus.FAILED, f"ES 失败应置 FAILED，实得 {doc.status}"

    monkeypatch.undo()  # 恢复 ES
    await _run_ingest(live_doc)  # 重跑（FAILED→PROCESSING→cleanup→DONE）
    doc2 = await _load_state(live_doc)
    assert doc2.status_enum == DocumentStatus.DONE, f"重跑应 DONE，实得 {doc2.status}"

    es_count = await _count_es(live_doc)
    milvus_count = await _count_milvus(live_doc)
    assert es_count == doc2.chunk_count, f"ES 残留 {es_count} != {doc2.chunk_count}"
    assert milvus_count == doc2.chunk_count, f"Milvus 残留 {milvus_count} != {doc2.chunk_count}"


async def test_4d_fault_milvus_fail_then_rerun(
    container, live_doc: DocContext, monkeypatch
) -> None:
    """注入：Milvus 写失败 → FAILED；重跑 → DONE；ES 不残留（首轮 ES 已被清理）。"""
    from core.exceptions import UpstreamError
    from ingest.worker import ingest_document

    async def _fail_milvus(*a, **k):
        raise RuntimeError("injected milvus failure")

    monkeypatch.setattr("core.storage.milvus.MilvusStore.insert", _fail_milvus)
    with pytest.raises(UpstreamError):
        await ingest_document({"container": live_doc.container}, live_doc.doc_id)
    doc = await _load_state(live_doc)
    assert doc.status_enum == DocumentStatus.FAILED, f"Milvus 失败应置 FAILED，实得 {doc.status}"

    monkeypatch.undo()
    await _run_ingest(live_doc)
    doc2 = await _load_state(live_doc)
    assert doc2.status_enum == DocumentStatus.DONE
    assert await _count_es(live_doc) == doc2.chunk_count
    assert await _count_milvus(live_doc) == doc2.chunk_count


# --------------------------------------------------------------------------- #
# 4.5 worker 幂等 / 重传
# --------------------------------------------------------------------------- #


async def test_duplicate_dispatch_idempotent(container, live_doc: DocContext) -> None:
    """重复投递：同一 doc_id 处理两次，切片不翻倍（ES _id / Milvus 主键幂等）。"""
    await _run_ingest(live_doc)
    es1 = await _count_es(live_doc)

    await _run_ingest(live_doc)  # 退避重跑 / 重复投递 → 第二次 ingest 同 doc_id
    doc2 = await _load_state(live_doc)
    es2 = await _count_es(live_doc)
    milvus2 = await _count_milvus(live_doc)

    assert es2 == es1, f"ES 重复投递有重复切片: {es1}->{es2}"
    assert milvus2 == doc2.chunk_count
    assert doc2.status_enum == DocumentStatus.DONE


async def test_file_hash_dedup_via_unique_constraint(container, live_doc: DocContext):
    """`(kb_id, file_hash)` 唯一约束保障重传去重：同 kb 同内容再插会冲突（4.5 file_hash 去重）。"""
    from sqlalchemy.exc import IntegrityError

    dup = Document(kb_id=live_doc.kb_id, file_name="dup.txt", file_hash=live_doc.file_hash)
    with pytest.raises(IntegrityError):
        async with container.mysql.session() as s:
            s.add(dup)
            await s.flush()
            await s.commit()


# --------------------------------------------------------------------------- #
# 4.6 端到端：摄入 → 检索可见
# --------------------------------------------------------------------------- #


async def test_e2e_upload_then_retrievable(container, live_doc: DocContext) -> None:
    """摄入完成后，同源 embedding query 到 Milvus / ES 检索命中该 doc 的切片。"""
    await _run_ingest(live_doc)
    doc = await _load_state(live_doc)
    assert doc.status_enum == DocumentStatus.DONE

    from ingest.embedding import embed_query

    vec = await embed_query("审批链到部门负责人")
    hits = await container.milvus.search(vec, kb_id=str(live_doc.kb_id), top_k=10,
                                         output_fields=["chunk_id", "doc_id"])
    doc_ids = {str(h.get("doc_id")) for h in hits}
    assert str(live_doc.doc_id) in doc_ids, f"Milvus 端到端未命中: {doc_ids}"

    kw = await container.es.keyword_search("报销流程", kb_id=str(live_doc.kb_id), top_k=10)
    assert any(str(h.get("doc_id")) == str(live_doc.doc_id) for h in kw), "ES 关键词未命中"