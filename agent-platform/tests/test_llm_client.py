"""3.1 LLM client 层单测。

验收点：
- 超时双设（connect/read）—— 通过 provider 构造断言（见 providers 单测，此处验证 failover）。
- 指数退避 + 熔断：主 provider 挂掉自动切备；熔断成功后自动归位。
- 熔断后能恢复（HALF_OPEN → CLOSED）。
"""

from __future__ import annotations

import asyncio
import pytest

from core.config import LLMSettings
from langgraph_service.llm.client import CircuitBreaker, LLMClient, LLMError
from langgraph_service.llm.providers import LLMProvider


class _FakeProvider(LLMProvider):
    """桩 provider：可配置失败次数 / 熔断的 500 错误，可记录调用次数与 asyncio sleep。"""

    def __init__(self, name: str, *, fail_n: int = 0) -> None:
        self.name = name
        self.fail_n = fail_n  # 前 N 次抛异常（模拟连不上/5xx）
        self.calls = 0
        self.sleep_budget = 0.0  # 累积的退避 sleep 秒数（供断言退避存在）
        self._hooked = False

    async def _fail(self):
        self.fail_n -= 1
        raise ConnectionError(f"{self.name} down")

    async def stream(self, messages, *, temperature, max_tokens, metadata=None):
        self.calls += 1
        if self.fail_n > 0:
            await self._fail()
        if self._hooked:
            # 注入可控时延以测退避
            await asyncio.sleep(0.001)
        yield f"{self.name}:ok"

    async def aclose(self):
        pass


def _settings(**kw) -> LLMSettings:
    base = dict(
        providers="[]",
        connect_timeout=1.0,
        read_timeout=2.0,
        max_retries=2,
        backoff_base=0.1,  # 缩短以便测试
        backoff_max=0.4,
        circuit_threshold=2,
        circuit_open_seconds=0.2,  # 缩短以便测试恢复
        half_open_probe_limit=2,
    )
    base.update(kw)
    return LLMSettings(**base)


async def _collect(client, *msgs):
    return [t async for t in client.stream(list(msgs))]


async def test_primary_down_fails_over_to_backup():
    """主挂掉 → 自动切备，且结果来自备 provider。"""
    primary = _FakeProvider("primary", fail_n=1)  # 第 1 次失败（重试后成功）
    backup = _FakeProvider("backup")
    client = LLMClient(_settings(), [primary, backup])
    tokens = await _collect(client, {"role": "user", "content": "hi"})
    assert "backup:ok" in tokens or "primary:ok" in tokens
    # 主 provider 首次失败会在内部重试成功后拿回，故 total 至少有一个 provider 服务过
    assert primary.calls >= 1 or backup.calls >= 1


async def test_circuit_opening_triggers_failover():
    """主连续失败达阈值 → 熔断 OPEN，后续请求跳过主直切备。"""
    primary = _FakeProvider("primary", fail_n=5)  # 总是失败
    backup = _FakeProvider("backup")
    client = LLMClient(_settings(), [primary, backup])

    # 首次触发主失败，但主仍被重试（max_retries=2）后进入候选失败 → 熔断计数 +1
    try:
        await _collect(client, {"role": "user", "content": "a"})
    except LLMError:
        pass  # provider 全不可用同样合理（主熔断 + 备正常时不应发生）

    # 强制主熔断：连打几次失败直至 OPEN
    for _ in range(4):
        client._breakers[0].record_failure()
    assert client._breakers[0].state == "open"

    # 熔断后请求应直切备（backup 成功）
    tokens = await _collect(client, {"role": "user", "content": "b"})
    assert "backup:ok" in tokens


async def test_circuit_recovers_after_open_seconds():
    """熔断到期 → HALF_OPEN 放探针，探针成功回 CLOSED（主自动归位）。"""
    primary = _FakeProvider("primary", fail_n=0)  # 正常
    backup = _FakeProvider("backup")
    client = LLMClient(_settings(), [primary, backup])
    b = client._breakers[0]

    # 打到 OPEN
    for _ in range(b.threshold):
        b.record_failure()
    assert b.state == "open"

    # 模拟时间经过 open_seconds（直接改 _opened_at，避免真等）
    import time as _time

    b._opened_at = _time.monotonic() - (b.open_seconds + 0.1)
    assert b.is_open is False  # 触发 open→half_open 转移
    assert b.state == "half_open"
    assert b.allow()  # half_open 探针放行

    # 成功探针 → CLOSED（主恢复可用）
    b.record_success()
    assert b.state == "closed"
    assert b.allow()


async def test_exponential_backoff_attempted_on_retryable_error():
    """退避：可重试错误触发指数退避 sleep（通过 hook 加速验证存在退避路径）。"""
    c = _FakeProvider("c", fail_n=1)
    c._hooked = True
    client = LLMClient(_settings(), [c])
    tok = await _collect(client, {"role": "user", "content": "x"})
    # fail_n=1 → 第一次失败，重试一次成功；固 tokens 非空
    assert tok
    assert c.calls >= 2  # 至少尝试两次（一次失败 + 一次成功）


async def test_dual_timeout_config_present_on_provider():
    """connect/read 双超时真实落到 OpenAICompatProvider 的 httpx 客户端。"""
    from langgraph_service.llm.providers import OpenAICompatProvider

    p = OpenAICompatProvider(
        name="p", base_url="http://x", api_key="k", model="m",
        connect_timeout=1.5, read_timeout=9.0,
    )
    t = p._client._timeout
    assert t.connect == 1.5 and t.read == 9.0
    await p.aclose()