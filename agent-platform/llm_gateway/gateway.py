"""LLM 网关核心（P1.3 · SUIG-33）。

`LLMGateway` 是网关能力聚合体，把既有 `LLMClient`（重试 / 熔断 / 多 provider 兜底）
再包一层网关语义：
- 调用方准入（API Key 白名单比对，不落明文）；
- 配额（按调用方 key，token / 调用次数分钟窗口限制）；
- Token 统计（每次调用累加，可自助查询）；
- 调用审计（写 AuditWriter）。

「熔断 / 超时 / 重试」本身复用 `LLMClient`——本模块不重复实现，只在其上做网关层
增强，保持单一事实来源。
"""

from __future__ import annotations

import datetime
import secrets
from collections.abc import Callable
from typing import Any

from core.logging import get_logger
from langgraph_service.llm.client import LLMClient

from .audit import AuditRecord, AuditWriter
from .errors import GatewayAuthError, UpstreamCallError
from .models import ChatCompletionResponse, ChatRequest, Usage
from .quota import PolicyQuotaManager

logger = get_logger(__name__)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：非空白字符 / 4（英文接近，中文可能略高偏保守）。

    网关把它作为配额消耗与统计口径；provider 侧若自带 usage 可再覆盖。
    """
    if not text:
        return 0
    return max(1, len("".join(text.split())) // 4)


class _CallStats:
    """一次调用的运行时统计：usage、provider、起止时间、错误。"""

    __slots__ = ("prompt_chars", "completion_len", "provider", "start", "end", "error")

    def __init__(self) -> None:
        self.prompt_chars = 0
        self.completion_len = 0
        self.provider = ""
        self.start = datetime.datetime.now()
        self.end: datetime.datetime | None = None
        self.error: str | None = None

    def usage(self) -> Usage:
        return Usage(
            prompt_tokens=estimate_tokens(" " * self.prompt_chars),
            completion_tokens=estimate_tokens("x" * self.completion_len),
        )


class LLMGateway:
    """LLM 网关注入：鉴权 → 配额 → 调用（LLMClient）→ 统计 → 审计。"""

    def __init__(
        self,
        client: LLMClient,
        quota: PolicyQuotaManager,
        writer: AuditWriter,
        *,
        api_key_map: dict[str, str] | None = None,
        default_actor_type: str = "system",
        default_actor_id: str = "llm-gateway",
        ip_factory: Callable[[], str | None] | None = None,
        now_factory: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        self._client = client
        self._quota = quota
        self._writer = writer
        self._api_keys = {k: v for k, v in (api_key_map or {}).items() if v}
        self._default_actor_type = default_actor_type
        self._default_actor_id = default_actor_id
        self._ip_factory = ip_factory or (lambda: None)
        self._now_factory = now_factory or (lambda: datetime.datetime.now())
        self._usage_by_key: dict[str, dict[str, int]] = {}

    # ---------- 准入 ----------

    def authenticate(self, api_key: str | None) -> str:
        """用 API Key 换取调用方 key（name）。非法 Key 抛 401。"""
        if not api_key:
            raise GatewayAuthError("缺失 API Key")
        if not self._api_keys:
            # 未配置任何 key：内部调用允许以任意非空值透传（单实例便利 / 测试）
            return api_key
        for name, key in self._api_keys.items():
            if secrets.compare_digest(key, api_key):
                return name
        raise GatewayAuthError("API Key 无效")

    # ---------- 统计 ----------

    def usage_metrics(self, key: str) -> dict[str, int]:
        """返回累计 token / 调用数统计。"""
        return dict(self._usage_by_key.get(key, {}))

    def _bump_usage(self, key: str, usage: Usage) -> None:
        cur = self._usage_by_key.setdefault(
            key, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        cur["calls"] += 1
        cur["prompt_tokens"] += usage.prompt_tokens
        cur["completion_tokens"] += usage.completion_tokens
        cur["total_tokens"] += usage.total_tokens

    def quota_snapshot(self, key: str) -> dict[str, int]:
        return self._quota.snapshot(key)

    # ---------- 核心调用 ----------

    async def chat(
        self,
        request: ChatRequest,
        api_key: str | None = None,
        *,
        caller_key: str | None = None,
        client_ip: str | None = None,
    ) -> ChatCompletionResponse:
        """非流式补全：一次性收集全部 token，返回聚合结果并落审计。

        - `api_key` 优先；缺省用 `caller_key`（已准入过的内部路径）。
        - 配额按最终估算 token 消耗；不允许则抛 `QuotaExceeded`（429）。
        """
        if caller_key is not None:
            key = caller_key
        else:
            key = self.authenticate(api_key)
        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        stats = _CallStats()
        stats.prompt_chars = sum(len(m.content) for m in request.messages)
        parts: list[str] = []
        try:
            async for token in self._client.stream(
                messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                metadata={},
            ):
                stats.completion_len += len(token)
                parts.append(token)
        except Exception as exc:  # noqa: BLE001 — 统一转 UpstreamCallError 并落审计
            stats.error = str(exc)
            self._record(key, request, stats, (client_ip or self._ip_factory()))
            raise UpstreamCallError(f"LLM 调用失败: {exc}") from exc

        stats.end = datetime.datetime.now()
        usage = stats.usage()
        # 先统计+落审计（成功/失败/超配额都留痕），配额作为最后的放行门
        self._bump_usage(key, usage)
        self._record(key, request, stats, client_ip or self._ip_factory())
        self._quota.enforce(key, usage.total_tokens)
        provider = stats.provider or self._client.last_provider or ""
        return ChatCompletionResponse(
            text="".join(parts),
            usage=usage,
            provider=provider,
            audit_run_id=f"gateway-{secrets.token_hex(4)}",
        )

    def _record(
        self,
        key: str,
        request: ChatRequest,
        stats: _CallStats,
        client_ip: str | None,
    ) -> None:
        """组装并写审计。成功 / 失败都留痕。"""
        usage = stats.usage().model_dump()
        diff: dict[str, Any] = {
            "usage": usage,
            "prompt_len": stats.prompt_chars,
            "completion_len": stats.completion_len,
            "caller_key": key,
        }
        if stats.error:
            diff["error"] = stats.error
        record = AuditRecord(
            actor_type=request.actor_type or self._default_actor_type,
            actor_id=request.actor_id or self._default_actor_id,
            action="llm.chat",
            resource_type="llm_provider",
            resource_id=stats.provider or "",
            diff_context=diff,
            ip=client_ip,
            tenant_id=0,
            created_at=stats.start,
            agent_run_id=request.run_ref,
            run_status=2 if stats.error else 1,
        )
        try:
            self._writer.append(record)
        except Exception as exc:  # noqa: BLE001 — 审计失败不能阻断主链路
            logger.warning("audit append failed", extra={"extra_fields": {"error": str(exc)}})

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("client close failed", extra={"extra_fields": {"error": str(exc)}})


__all__ = ["LLMGateway", "estimate_tokens"]