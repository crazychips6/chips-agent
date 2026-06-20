"""Plugin 协议定义 — HookPlugin 接口 + PluginContext

插件是 Python 文件，放在 ~/.chips/plugins/ 或 ./plugins/ 目录下，
导出 ``register(ctx: PluginContext)`` 函数。

约定：
  - 文件名即插件名
  - ``register(ctx)`` 内部调用 ``ctx.register_tool()`` / ``ctx.register_hook()``
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class HookPlugin(Protocol):
    """挂钩到 agent 生命周期的插件。"""

    def on_register(self, registry) -> None:
        ...

    def on_tool_call_pre(
        self, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any] | None:
        ...

    def on_tool_call_post(self, tool_name: str, result: str) -> str | None:
        ...

    def on_llm_call_pre(
        self, messages: list[dict], model: str, kwargs: dict[str, Any]
    ) -> dict[str, Any] | None:
        """LLM 调用前触发。可返回修改后的 kwargs。"""
        ...

    def on_llm_call_post(
        self, messages: list[dict], model: str,
        result: Any, duration_ms: int,
    ) -> None:
        """LLM 调用后触发，携带耗时和结果。"""
        ...

    def on_response(self, response: str) -> str | None:
        ...

    def on_session_end(self, messages: list[dict]) -> None:
        ...


class PluginContext:
    """插件注册上下文。

    通过 ``register(ctx)`` 回调传递给插件，提供注册工具和挂钩的方法。
    PluginManager 在 ``register()`` 返回后从 ctx 收集注册结果。
    """

    def __init__(
        self,
        registry=None,
        *,
        dry_run: bool = False,
    ):
        self._registry = registry
        self._dry_run = dry_run
        self._tool_names: set[str] = set()
        self._tools_info: list[dict] = []
        self._hook_plugins: list[HookPlugin] = []
        self._skills: list = []

    def register_tool(
        self,
        name: str,
        schema: dict,
        handler: Callable,
        toolset: str = "plugin",
    ) -> None:
        """注册一个工具。

        Args:
            name: 工具名
            schema: OpenAI function-calling 格式的 schema
            handler: 调用处理函数，接收 ``(args: dict) -> str``
            toolset: 所属工具集（默认 ``"plugin"``）
        """
        self._tool_names.add(name)
        self._tools_info.append({
            "name": name,
            "schema": schema,
            "definition_count": 1,
        })
        if self._registry is not None and not self._dry_run:
            self._registry.register(
                name=name,
                toolset=toolset,
                schema=schema,
                handler=handler,
            )

    def register_hook(self, hook: HookPlugin) -> None:
        """注册一个 HookPlugin。"""
        self._hook_plugins.append(hook)

    def register_skill(
        self,
        name: str,
        description: str,
        path: str | None = None,
    ) -> None:
        """注册一个技能（指向 SKILL.md 文件）。

        技能是 ``SKILL.md`` 文件（YAML frontmatter + markdown 正文）。
        插件可以注册自己打包的 SKILL.md，系统会自动将其加入技能索引。

        Args:
            name: 技能名
            description: 技能描述
            path: SKILL.md 文件路径（可选）。如果提供，系统会复制到技能目录；
                  如果不提供，视为声明式注册（由外部 SKILL.md 覆盖）。
        """
        self._skills.append({
            "name": name,
            "description": description,
            "path": path,
        })
