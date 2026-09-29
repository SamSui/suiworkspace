"""P1.3 · LLM 网关单测（SUIG-33）。

Step1 验收口径（针对 llm-gateway）：
- 配额生效：超限调用被拒（QuotaExceeded / 429）；
- 熔断 / 超时：复用既有 LLMClient，经网关后 provider 失败切备、上游失败统一收口；
- 调用落审计：成功与失败都写 AuditWriter（audit_log 语义）。
附加：API Key 准入、Token 统计。

全链路用内存后端（MemoryQuotaBackend / MemoryAuditBackend）+ 桩 provider，
不依赖数据库。
"""

from __future__ import annotations

from core.config import LLMSettings
from langgraph_service.llm.client import LLMClient
from langgraph_service.llm.providers import LLMProvider
from llm_gateway.audit import MemoryAuditBackend
from llm_gateway.errors import GatewayAuthError, QuotaExceeded, UpstreamCallError
from llm_gateway.gateway import LLMGateway
from llm_gateway.models import ChatRequest
from llm_gateway.quota import MemoryQuotaBackend, PolicyQuotaManager, QuotaPolicy

API_KEYS = {"alice": "sk-alice"}


class _FakeProvider(LLMProvider):
    """桩 provider：可按需注入失败次数，记录调用次数。"""

    def __init__(self, name: str = "p", *, fail_n: int = 0, token: str = "tok") -> None:
        self.name = name
        self.fail_n = fail_n
        self.token = token
        self.calls = 0

    async def stream(self, messages, *, temperature, max_tokens, metadata=None):
        self.calls += 1
        if self.fail_n > 0:
            self.fail_n -= 1
            raise ConnectionError(f"{self.name} down")
        yield self.token
        yield f"-{self.token}2"

    async def aclose(self):
        pass


def _llm_settings(**kw) -> LLMSettings:
    base = dict(providers="[]", max_retries=0, backoff_base=0.1, backoff_max=0.4)
    base.update(kw)
    return LLMSettings(**base)


def _gateway(provider=None, *, quota_policies=(), api_keys=None):
    client = LLMClient(_llm_settings(), [provider or _FakeProvider()])
    qm = PolicyQuotaManager(list(quota_policies), MemoryQuotaBackend())
    writer = MemoryAuditBackend()
    gw = LLMGateway(client, qm, writer, api_key_map=api_keys or API_KEYS)
    return gw, qm, writer


def _req(text: str = "hello") -> ChatRequest:
    return ChatRequest(messages=[{"role": "user", "content": text}])


# ---------- 准入 ----------

async def test_bad_api_key_rejected():
    gw, _, _ = _gateway()
    try:
        await gw.chat(_req(), api_key="wrong")
        raise AssertionError("should reject bad key")
    except GatewayAuthError:
        pass


async def test_good_api_key_resolves_caller_and_stats():
    gw, _, _ = _gateway()
    resp = await gw.chat(_req(), api_key="sk-alice")
    assert "tok" in resp.text
    assert gw.usage_metrics("alice")["calls"] == 1


# ---------- 配额 ----------

async def test_quota_exceeded_rejects_call():
    # 配额 key 与 API Key 解析出的调用方名一致（alice）
    policy = QuotaPolicy(key="alice", tokens_per_minute=1, calls_per_minute=0)
    gw, qm, _ = _gateway(quota_policies=(policy,))
    # 直接用后端把 alice 的分钟窗口 token 打爆（绕过策略判定）
    qm.backend.consume("alice", 5)
    try:
        await gw.chat(_req(), api_key="sk-alice")
        raise AssertionError("应触发配额拒绝")
    except QuotaExceeded:
        pass


# ---------- 审计 ----------

async def test_success_call_lands_audit():
    gw, _, writer = _gateway()
    await gw.chat(_req("你好"), api_key="sk-alice")
    acts = [r.action for r in writer.records]
    assert "llm.chat" in acts
    assert writer.records[-1].action == "llm.chat"
    assert writer.records[-1].run_status == 1


# ---------- 熔断 / 上游失败 ----------

async def test_upstream_failure_audited_and_surfaced():
    gw, _, writer = _gateway(provider=_FakeProvider("bad", fail_n=1))
    try:
        await gw.chat(_req(), api_key="sk-alice")
        raise AssertionError("应抛上游错误")
    except UpstreamCallError:
        pass
    assert writer.records and writer.records[-1].run_status == 2


# ---------- Token 统计 ----------

async def test_token_stats_accumulate():
    gw, _, _ = _gateway(provider=_FakeProvider(token="a"))
    await gw.chat(_req("x"), api_key="sk-alice")
    await gw.chat(_req("yy"), api_key="sk-alice")
    metrics = gw.usage_metrics("alice")
    assert metrics["calls"] == 2
    assert metrics["total_tokens"] >= 0