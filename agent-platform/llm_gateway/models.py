"""LLM 网关数据契约（P1.3 · SUIG-33）。

输入 / 输出 与错误用 Pydantic v2 建模，作为该服务对外的稳定契约：
- `ChatMessage` —— 会话消息（与 LangChain 语义一致的 role/content 二元组）；
- `ChatRequest` —— 非流式补全入参（含可选覆盖超时 / 单次配额覆盖）；
- `ChatResult` —— 非流式补全出参（含 usage 与落库后的审计 run_id）；
- `Usage` —— token 统计单元（prompt / completion / total）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """一条对话消息。content 为纯文本（字符串）。"""

    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str = Field(min_length=0)


class Usage(BaseModel):
    """一次补全的 token 统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ChatRequest(BaseModel):
    """对话补全入参。调用方需经网关准入（api key / 内部 token）后调用。"""

    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = None
    # 审计关联：调用方希望以谁的名义落审计 / 关联到哪个 run/conversation
    actor_type: Literal["user", "agent", "system"] | None = None
    actor_id: str | None = None
    run_ref: str | None = None  # 可选：关联到既有 agent_run（P1.1 表）的 run_id
    conv_ref: str | None = None


class ChatCompletionResponse(BaseModel):
    """流式完成后聚合的最终出参（服务端把流攒出入库后返回给 HTTP 调用方）。"""

    text: str = ""
    usage: Usage = Field(default_factory=Usage)
    provider: str = ""
    audit_run_id: str | None = None


class QuotaSnapshot(BaseModel):
    """某调用方当刻的配额水位（供调用方自查询 / 见然后再等）。"""

    key: str
    tokens_per_minute: int
    calls_per_minute: int
    tokens_used_this_minute: int
    calls_used_this_minute: int
    allowed: bool
    retry_after_seconds: float = 0.0


__all__ = [
    "ChatCompletionResponse",
    "ChatMessage",
    "ChatRequest",
    "QuotaSnapshot",
    "Usage",
]