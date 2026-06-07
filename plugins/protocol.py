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
