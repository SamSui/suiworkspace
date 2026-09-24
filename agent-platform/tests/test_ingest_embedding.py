"""4.3 Embedding 单测。

覆盖验收口径：
- 批量向量化，维度与 `MILVUS_DIM`（`settings.milvus.dim`，默认 768）一致；
- 空输入 → 空列表；批量按 chunk 对齐；
- 失败可重试：注入 `langgraph_service.retrieval._stub_embed` 失败 → `UpstreamError`，
  上层（worker 退避重跑）重试语义成立（4.4 注错测试覆盖端到端）。
"""

from __future__ import annotations

import pytest

from ingest.embedding import embed_query, embed_texts
from langgraph_service.retrieval import _stub_embed


@pytest.fixture(scope="module")
def dim() -> int:
    from core.config import get_settings

    return get_settings().milvus.dim


async def test_embed_dim_aligned(dim: int) -> None:
    assert dim > 0
    vecs = await embed_texts(["报销流程", "审批链到部门经理"])
    assert len(vecs) == 2
    for v in vecs:
        assert len(v) == dim, f"向量维度 {len(v)} != MILVUS_DIM({dim})"


async def test_embed_query_dim(dim: int) -> None:
    assert len(await embed_query("报销")) == dim


async def test_embed_empty() -> None:
    assert await embed_texts([]) == []


async def test_embed_l2_normalized(dim: int) -> None:
    vecs = await embed_texts(["报销流程"])
    # L2 归一 → 模长 ≈ 1（与 `_stub_embed` 一致，保证检索 COSINE 语义）
    v = vecs[0]
    norm = sum(x * x for x in v) ** 0.5
    assert abs(norm - 1.0) < 1e-6


async def test_embed_deterministic_same_phrase(dim: int) -> None:
    """同短语同向量 → 摄入向量与检索 query 向量一致（e2e 闭环前提）。"""
    [a] = await embed_texts(["报销流程"])
    b = await embed_query("报销流程")
    assert a == b


async def test_embed_batch_size_respected(dim: int) -> None:
    texts = [f"t{i}" for i in range(5)]
    vecs = await embed_texts(texts, batch_size=2)
    assert len(vecs) == 5
    assert all(len(v) == dim for v in vecs)


async def test_embed_failure_is_retryable(dim: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """batch 内失败 → UpstreamError（可重试语义）；移除故障（模拟退避重跑后）同输入成功。"""
    from core.exceptions import UpstreamError

    real = _stub_embed

    async def _flaky(text, d_):
        raise TimeoutError("embed ff")

    monkeypatch.setattr("ingest.embedding._stub_embed", _flaky)
    with pytest.raises(UpstreamError):
        await embed_texts(["报销", "审批"], batch_size=1)

    monkeypatch.setattr("ingest.embedding._stub_embed", real)
    vecs = await embed_texts(["报销", "审批"], batch_size=1)
    assert len(vecs) == 2
    assert all(len(v) == dim for v in vecs)


def test_embed_delegates_to_retrieval_source(dim: int) -> None:
    """与检索侧位级一致：ingest.embedding 直接复用 retrieval._stub_embed。"""
    from ingest import embedding

    assert embedding._stub_embed is _stub_embed