"""LLM 网关配置（P1.3 · SUIG-33）。

独立于既有 `core.config` 的事件驱动：网关作为可独立部署的服务，自带分层配置，
环境变量前缀用 `LLMGW_` 隔离，不污染 `core.config` 既有的 `LLM_` 前缀。

要点：
- `quotas` 为一个 JSON 数组 `[{key, tokens_per_min, calls_per_min}]`，最简启动
  可不配置（仅记录、不限流）。
- `audit` 开关控制是否落 `agent_run` / `audit_log`；进程内直接依赖 P1.1 表契约，
  测试用内存后端替代。
"""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmGatewaySettings(BaseSettings):
    """LLM 网关分层配置。env 前缀 `LLMGW_`。"""

    model_config = SettingsConfigDict(
        env_prefix="LLMGW_", env_file=".env", extra="ignore", case_sensitive=False
    )

    # ---- 服务 ----
    host: str = "0.0.0.0"
    port: int = 8200

    # ---- 准入：网关自持的调用方 API Key（逗号分隔：name1=key1,name2=key2）----
    # 只存哈希的对应校验逻辑在 runtime；此处仅放“能唯一定位调用方”的映射名。
    api_keys: str = ""

    # ---- 配额（JSON）：[{key, tokens_per_minute, calls_per_minute}] ----
    quotas: str = "[]"

    # 单次调用默认上限（防御值，防止单次爆配额）
    max_tokens_default: int = 4096
    default_timeout_seconds: float = 60.0

    # ---- 审计 ----
    # audit_backend: memory | mysql。memory 仅为本机/单测调试；生产须 mysql（依赖 P1.1 表）。
    audit_backend: str = "memory"
    # run 级落 agent_run 的开关
    persist_agent_run: bool = True
    # 审计 actor（默认 system；由请求方覆盖）
    default_actor_type: str = "system"
    default_actor_id: str = "llm-gateway"

    # ---- 关联既有 LLM 配置（交由 langgraph_service.llm 的 provider 链使用）----
    # 直接复用核心 LLM 设置分节读取，避免重复 key。
    llm_providers: str = ""
    connect_timeout: float = 5.0
    read_timeout: float = 60.0
    max_retries: int = 2
    backoff_base: float = 0.5
    backoff_max: float = 4.0
    circuit_threshold: int = 3
    circuit_open_seconds: float = 30.0
    half_open_probe_limit: int = 3
    temperature: float = 0.7
    default_max_tokens: int = 1024

    @property
    def quota_list(self) -> list[dict[str, object]]:
        """解析 quotas JSON；非法/空 → []（不限额）。"""
        if not self.quotas or not self.quotas.strip():
            return []
        try:
            loaded = json.loads(self.quotas)
            return loaded if isinstance(loaded, list) else []
        except (ValueError, TypeError):
            return []

    @property
    def api_key_map(self) -> dict[str, str]:
        """解析 `name1=key1,name2=key2` → {name: key}。"""
        mapping: dict[str, str] = {}
        for item in self.api_keys.split(","):
            item = item.strip()
            if not item:
                continue
            name, _, key = item.partition("=")
            if key:
                mapping[name.strip()] = key.strip()
        return mapping


@lru_cache(maxsize=1)
def get_llm_gateway_settings() -> LlmGatewaySettings:
    """进程内单例。测试用 `cache_clear()` 重置。"""
    return LlmGatewaySettings()


__all__ = ["LlmGatewaySettings", "get_llm_gateway_settings"]