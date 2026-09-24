"""增量 5.3 / 5.4 可观测单测。

覆盖验收 5.3（trace_id 贯穿 + 各环节耗时 span）与 5.4（缓存命中率 / 检索 P99 /
错误率 / 依赖耗时 / /metrics 文本导出）。全部离线可跑，不依赖六服务：

- span 生命周期、trace_id 一致性、父子链还原；
- Prometheus 指标（命中/未命中、P99、错误、依赖）与 `generate_latest()` 导出；
- 桥接既有 `langgraph_service.metrics` 直方图；
- 网关级端到端：真实起一次 ASGI app，走 /healthz/live 与 /metrics，验证 span 与计数。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from core.logging import set_trace_id
from observability import otel
from observability.prom import metrics as prom_metrics


def _clean():
    otel.reset_observability()
    prom_metrics.clear()


# ---------------- 5.3：span 生命周期 / trace_id 贯穿 ----------------

async def test_span_records_trace_and_duration():
    _clean()
    set_trace_id("trace-xyz")
    try:
        with otel.span("gateway"):
            await asyncio.sleep(0.01)
    finally:
        set_trace_id("")  # 复位日志 trace_id，避免污染后续用例
    recs = otel.get_sink().spans()
    assert len(recs) == 1
    assert recs[0].name == "gateway"
    assert recs[0].trace_id == "trace-xyz"  # 与日志 trace_id 一致
    assert recs[0].duration_ms > 0


async def test_span_child_chain_reconstruct():
    _clean()
    with otel.span("gateway") as gw:
        with gw.child("dependency.es") as es_span:
            with es_span.child("network"):
                pass
        with gw.child("generate"):
            pass

    spans = otel.get_sink().spans()
    by_name = {s.name: s for s in spans}
    assert set(by_name) >= {"gateway", "dependency.es", "network", "generate"}
    assert by_name["dependency.es"].parent_name == "gateway"
    assert by_name["network"].parent_name == "dependency.es"
    assert by_name["generate"].parent_name == "gateway"

    chain = otel.format_chain(gw)
    assert "gateway" in chain and "dependency.es" in chain  # 缩进链路可读


async def test_async_span_context_manager():
    _clean()
    async with otel.async_span("node.retrieve"):
        pass
    assert len(otel.get_sink().take(name="node.retrieve")) == 1


async def test_spanned_decorator_measures():
    _clean()

    @otel.spanned
    async def do_llm():
        await asyncio.sleep(0.005)

    await do_llm()
    spans = otel.get_sink().take()
    assert len(spans) == 1 and spans[0].name.endswith("do_llm") and spans[0].duration_ms > 0


# ---------------- 5.4：指标与导出 ----------------

def test_cache_metrics():
    _clean()
    prom_metrics.cache_hit()
    prom_metrics.cache_hit()
    prom_metrics.cache_miss()
    c = prom_metrics.counts()
    assert c["cache_hits"] == 2
    assert c["cache_misses"] == 1


def test_retrieval_p99():
    _clean()
    for s in [0.02, 0.03, 0.05, 0.2, 0.4, 0.6, 0.8, 1.2, 2.9, 3.1]:
        prom_metrics.observe_retrieval(s)
    p99 = prom_metrics.p99_seconds("retrieval")
    assert 2.9 <= p99 <= 3.1  # 第 10 个样本的 99% ≈ 末位


def test_error_and_dependency_metrics():
    _clean()
    prom_metrics.error("gateway")
    prom_metrics.error("orchestr")
    prom_metrics.observe_dependency("llm", "echo", 0.5)
    assert prom_metrics.counts()["errors"] == 2
    assert prom_metrics.p99_seconds("dependency:llm:echo") == 0.5


def test_generate_latest_contains_metric_names():
    _clean()
    prom_metrics.cache_hit()
    prom_metrics.observe_retrieval(0.3)
    prom_metrics.error("gateway")
    body, ctype = prom_metrics.generate_latest()
    text = body.decode("utf-8")
    assert "kb_cache_hits_total" in text
    assert "kb_retrieval_seconds" in text
    assert "kb_errors_total" in text
    assert "text/plain" in ctype


def test_bridge_in_process_metrics():
    from langgraph_service import metrics as lm

    lm.reset()
    lm.record("node.router.tokens", 10)
    _clean()
    body, _ = prom_metrics.generate_latest()  # 内部会桥接 lm.snapshot()
    assert "kb_inproc_metric_seconds" in body.decode("utf-8")


# ---------------- 网关级端到端 ----------------

def test_api_metrics_and_spans_end_to_end():
    """真实启动网关（无存储也能活），请求 /healthz/live 与 /metrics。"""
    from api.main import app

    _clean()
    with TestClient(app) as client:
        r = client.get("/healthz/live")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"

        r2 = client.get("/metrics")
        assert r2.status_code == 200
        assert "kb_requests_total" in r2.text
        assert "kb_requests_inflight" in r2.text

    # 中间件为每请求开一个根 span
    spans = otel.get_sink().spans()
    assert any(s.name == "http.request" for s in spans)


# ---------------- 导入幂等 ----------------

def test_observability_imports_safe():
    import observability.otel
    import observability.prom

    assert hasattr(observability.otel, "span")
    assert hasattr(observability.prom, "metrics")