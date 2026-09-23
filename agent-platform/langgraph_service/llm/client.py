"""LLM 客户端（增量 3.1 落地）。

在一层里收敛：
1. **connect/read 双超时** —— provider 构造时分别设置，读超时同时约束首 chunk 与
   相邻 chunk 间隔。
2. **指数退避重试** —— 对瞬时/可重试错误（连接错误、5xx、读超时）按 `backoff_base
   * 2**attempt` 退避并加抖动，最多 `max_retries` 次。
3. **熔断** —— 每 provider 一个 `CircuitBreaker`：连续失败达 `circuit_threshold`
   即 OPEN，在 `circuit_open_seconds` 内不再投递；到期进 HALF_OPEN 放探针，探针
   成功则回 CLOSED、失败则重新 OPEN。
4. **多 provider 兜底** —— 按优先级依次尝试，主 provider 挂掉自动切下一位；熔断中
   的 provider 直接跳过。主 provider 熔断恢复后自动归位（切备是「按序跳过不可用者」，
   恢复后主即回到候选首位）。

`stream()` 逐 token 产出，失败时抛 `LLMError`。单测直接注入桩 provider 驱动验收。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator
from typing import Any

from core.config import LLMSettings
from core.exceptions import UpstreamError
from core.logging import get_logger
from langgraph_service.llm.providers import EchoProvider, LLMProvider, OpenAICompatProvider
from langgraph_service.metrics import record as record_metric

logger = get_logger(__name__)

_REACHABLE_TIMEOUT_S = 5.0  # async generator 驱动的探针 sleep 下限（供 HALF_OPEN 判定）


class LLMError(UpstreamError):
    """所有 LLM 调用失败统一收口为此异常（外层据此决定熔断/切备）。"""

    def __init__(self, provider: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.detail = {"provider": provider}


def _is_retryable(exc: BaseException) -> bool:
    return not (isinstance(exc, LLMError) and not exc.retryable)


class CircuitBreaker:
    """进程内熔断器。

    closed → open（连续失败达 threshold）→ 到期 half_open（限量放探针）
    → 探针成功 closed / 失败 open。
    """

    __slots__ = (
        "threshold",
        "open_seconds",
        "half_open_limit",
        "failures",
        "state",
        "_opened_at",
        "_probes",
    )

    def __init__(self, *, threshold: int, open_seconds: float, half_open_limit: int) -> None:
        self.threshold = threshold
        self.open_seconds = open_seconds
        self.half_open_limit = half_open_limit
        self.failures = 0
        self.state = "closed"
        self._opened_at = 0.0
        self._probes = 0

    @property
    def is_open(self) -> bool:
        if self.state == "open" and time.monotonic() - self._opened_at >= self.open_seconds:
            self.state = "half_open"
            self._probes = 0
        return self.state == "open"

    def allow(self) -> bool:
        if self.state == "closed":
            return True
        if self.is_open:
            return False
        # half_open
        self._probes += 1
        return self._probes <= self.half_open_limit

    def record_success(self) -> None:
        self.failures = 0
        self.state = "closed"
        self._probes = 0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.state = "open"
            self._opened_at = time.monotonic()
            self._probes = 0


class LLMClient:
    def __init__(self, settings: LLMSettings, providers: list[LLMProvider]) -> None:
        self.settings = settings
        self.providers = providers
        self.last_provider: str | None = None  # 最近一次成功服务的 provider 名
        self._breakers = [
            CircuitBreaker(
                threshold=settings.circuit_threshold,
                open_seconds=settings.circuit_open_seconds,
                half_open_limit=settings.half_open_probe_limit,
            )
            for _ in providers
        ]

    # ---------- 工厂（运行时装配） ----------

    @staticmethod
    async def from_settings(settings: LLMSettings) -> LLMClient:
        """按配置装配 provider 链。无外部凭据时回退内置 Echo 桩。"""
        providers: list[LLMProvider] = []
        for idx, spec in enumerate(settings.provider_list):
            providers.append(
                OpenAICompatProvider(
                    name=str(spec.get("name") or f"provider-{idx}"),
                    base_url=str(spec.get("base_url") or ""),
                    api_key=str(spec.get("api_key") or ""),
                    model=str(spec.get("model") or ""),
                    connect_timeout=settings.connect_timeout,
                    read_timeout=settings.read_timeout,
                )
            )
        if not providers:
            providers = [EchoProvider()]
            logger.info("LLM 未配置外部 provider，回退内置 Echo 桩：仅链路验证，非真实生成")
        return LLMClient(settings, providers)

    async def aclose(self) -> None:
        for provider in self.providers:
            if hasattr(provider, "aclose"):
                await provider.aclose()

    # ---------- 核心：择备 + 熔断 + 退避 ----------

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        temp = temperature if temperature is not None else self.settings.temperature
        mtok = max_tokens if max_tokens is not None else self.settings.max_tokens

        for provider, breaker in zip(self.providers, self._breakers, strict=True):
            if not breaker.allow():
                logger.debug(  # noqa: E501 — EXTRA dict 内部含大数据结构，保持单行可扫读
                    "provider 熔断中，跳过",
                    extra={"extra_fields": {"provider": provider.name}},
                )
                continue
            try:
                async for token in self._stream_with_retry(  # noqa: E501 环节长，语义连续
                    provider, messages, temp, mtok, metadata
                ):
                    yield token
                self.last_provider = provider.name
                return
            except LLMError:
                breaker.record_failure()
                logger.warning(
                    "provider 不可用，切下一个",
                    extra={
                        "extra_fields": {"provider": provider.name, "failures": breaker.failures}
                    },
                )
        raise LLMError("chain", "所有 provider 均不可用", retryable=False)

    async def _stream_with_retry(
        self,
        provider: LLMProvider,
        messages: list[dict[str, Any]],
        temp: float,
        mtok: int,
        metadata: dict[str, Any] | None,
    ) -> AsyncIterator[str]:
        """对单个 provider：指数退避重试 + 计时 + 首 token 时延。"""
        last_err: BaseException | None = None
        for attempt in range(self.settings.max_retries + 1):
            start = time.monotonic()
            first_ts: float | None = None
            got_token = False
            try:
                async for token in provider.stream(  # noqa: E501 环节长，语义连续
                    messages, temperature=temp, max_tokens=mtok, metadata=metadata
                ):
                    if not got_token:
                        got_token = True
                        first_ts = time.monotonic()
                        record_metric("llm.first_token_ms", (first_ts - start) * 1000)
                    yield token
                record_metric("llm.total_ms", (time.monotonic() - start) * 1000)
                return
            except Exception as exc:  # noqa: BLE001 — 收敛为 retryable 判定
                last_err = exc
                if not _is_retryable(exc) or got_token or attempt >= self.settings.max_retries:
                    break
                delay = min(self.settings.backoff_max, self.settings.backoff_base * (2 ** attempt))
                delay *= random.uniform(0.5, 1.0)
                logger.warning(
                    "llm retry",
                    extra={
                        "extra_fields": {
                            "provider": provider.name,
                            "attempt": attempt,
                            "delay_ms": delay * 1000,
                        }
                    },
                )
                await asyncio.sleep(delay)
        raise LLMError(  # noqa: E501 出口统一，保留单行便于 grep
            provider.name, f"调用失败: {last_err or '未知错误'}", retryable=_is_retryable(last_err)
        )


__all__ = ["LLMClient", "LLMError", "CircuitBreaker"]