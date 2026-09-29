"""工具注册表与授权（P1.3 · SUIG-33）。

`ToolRegistry` 承载：
- 工具定义存储（由配置/运行时注入，语义对齐 P1.1 `tool`/`tool_permission` 表）；
- **统一授权白名单**：每个调用方（actor）显式授权某工具后方可调用，默认一律拒绝。
- SQL/代码工具的“默认禁”：即使授权，仍需工具 `isolated=True` 且开启隔离执行。

授权语义（对齐 P1.1 `tool_permission.level`）：
- 未在 allow 列表 → 无权（404 / 403，与不存在同义收敛为 403，避免探测）。
"""

from __future__ import annotations

from collections.abc import Mapping

from .models import ToolSpec


class ToolRegistry:
    """按 name 索引工具 + 每 actor 的工具白名单。默认拒绝。"""

    def __init__(
        self,
        tools: Mapping[str, ToolSpec] | None = None,
        *,
        allow: Mapping[str, list[str]] | None = None,
        default_actor: str = "default",
    ) -> None:
        """
        - `tools`: {工具名: ToolSpec}
        - `allow`: {actor: [工具名, ...]}，未列出的 actor 无任何工具权限。
        """
        self._tools: dict[str, ToolSpec] = dict(tools or {})
        self._allow: dict[str, set[str]] = {k: set(v) for k, v in (allow or {}).items()}
        self._default_actor = default_actor

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list(self, actor: str | None = None) -> list[ToolSpec]:
        """对该 actor 可见（已授权）的工具列表；无 actor 时返回全部注册定义。"""
        if actor is None:
            return list(self._tools.values())
        allowed = self._allow.get(actor, set())
        return [s for n, s in self._tools.items() if n in allowed]

    def can(self, actor: str, tool_name: str) -> bool:
        """统一准入第一道门：是否授权。未授权 → False（调用方应收敛为拒）。"""
        return tool_name in self._allow.get(actor or self._default_actor, set())

    def approve(self, actor: str, tool_name: str) -> None:
        """显式授权（运行时配置/审计人工放行的 hook）。"""
        s = self._allow.setdefault(actor, set())
        s.add(tool_name)

    def revoke(self, actor: str, tool_name: str) -> None:
        s = self._allow.get(actor)
        if s:
            s.discard(tool_name)


__all__ = ["ToolRegistry"]