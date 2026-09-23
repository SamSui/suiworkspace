"""Provider 抽象与实现。

`LLMProvider` 是统一的对话补全接口（流式返回 token）。
- `OpenAICompatProvider`：走 OpenAI 兼容 `/chat/completions` SSE（主/备 provider 真接）。
- `EchoProvider`：无外部凭据时的内置桩——把 prompt 拼进一段回显文本并把引用切片
  原样带出，保证编排在无付费凭据环境也能端到端跑通（验收 3.5 引用回链 chunk_id）。

3.1 的验收点（主挂自动切备、熔断后恢复）只依赖 `LLMProvider` 接口与
`LLMClient` 的择备/熔断逻辑，不依赖某个具体 provider 实现，故单测用桩驱动。
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from typing import Any

from core.logging import get_logger

logger = get_logger(__name__)


class LLMProvider(abc.ABC):
    """一次补全 = 若干 token。`stream()` 逐 token 吐，异常在 `LLMClient` 兜。"""

    name: str = "provider"

    @abc.abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """按 token 产出文本片段。返回前不发剩余消息。"""


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容 HTTP provider（httpx 流式）。

    超时注解：connect 限建连，read 限首 chunk / 相邻 chunk 间隔——两者独立设置，
    满足「connect/read 双超时」验收；退避由 `LLMClient` 处理。
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        connect_timeout: float,
        read_timeout: float,
    ) -> None:
        import httpx

        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=connect_timeout, read=read_timeout,
                                  write=connect_timeout, pool=connect_timeout),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=64),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        async with self._client.stream(
            "POST", url, json=payload, headers={"Authorization": f"Bearer {self.api_key}"}
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    return
                import json

                try:
                    chunk = json.loads(data)
                except ValueError:  # noqa: PERF203 — 非关键，跳过坏块
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text


class EchoProvider(LLMProvider):
    """内置桩：无外部凭据时兜底，保证编排端到端可跑。

    输出把 query 回显为答案，并把提示词里出现的检索切片（`[ref:chunk_id]...[/ref]`
    惯例）原样带回，让 generate 的引用列表有真实 chunk_id 可回链。
    """

    name = "echo"

    def __init__(self) -> None:
        self._tokens: list[str] | None = None
        self._i = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        if self._tokens is None:
            self._tokens = self._build(messages)
            self._i = 0
        if self._i < len(self._tokens):
            token = self._tokens[self._i]
            self._i += 1
            yield token

    def _build(self, messages: list[dict[str, Any]]) -> list[str]:
        # 从最后一条 user 消息抽取检索切片 refs；无则纯回显。
        refs: list[str] = []
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(str(c) for c in content)
            import re

            refs = re.findall(r"\[ref:([^\]]+)\]", str(content))
            break
        parts = ["【Echo 桩答案】"]
        if refs:
            parts.append("引用了切片:")
            parts.append("、" .join(refs))
        parts.append("（本环境未配置外部 LLM 凭据，由内置 Echo provider 兜底，仅用于链路验证。）")
        return [parts[0], *parts[1:]]

    async def aclose(self) -> None:  # noqa: D401 — Echo 无真实连接
        pass