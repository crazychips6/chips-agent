"""Plugin 协议定义 — ToolPlugin 和 HookPlugin 接口

插件是 Python 文件，放在 ~/.chips/plugins/ 或 ./plugins/ 目录下，
导出 ``__plugin__`` 属性（实现 ToolPlugin 或 HookPlugin 协议的实例）。

约定：
  - 文件名即插件名，但插件自身 ``.name`` 属性是独立标识
  - __plugin__ 可以是单个实例，也可以是 list[ToolPlugin | HookPlugin]
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolPlugin(Protocol):
    """提供工具的插件。

    每个 ToolPlugin 定义一个或多个工具的 schema 和实现。
    加载后，PluginManager 会自动将工具注册到 ToolRegistry。
    """

    name: str
    description: str

    def tool_definitions(self) -> list[dict]:
        """返回工具 schema 列表（OpenAI function-calling 格式）。

        每个 dict 格式：:

            {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        """
        ...

    def execute(self, tool_name: str, args: dict[str, Any]) -> str:
        """执行工具调用，返回结果字符串。"""
        ...


@runtime_checkable
class HookPlugin(Protocol):
    """挂钩到 agent 生命周期的插件。"""

    def on_register(self, registry) -> None:
        """插件被加载时调用，可在此处访问 registry。"""
        ...

    def on_tool_call_pre(
        self, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any] | None:
        """工具调用前触发。

        Args:
            tool_name: 将要调用的工具名
            args: 当前参数

        Returns:
            修改后的 args，或 None 表示不干预。
        """
        ...

    def on_tool_call_post(self, tool_name: str, result: str) -> str | None:
        """工具调用后触发。

        Returns:
            修改后的结果字符串，或 None 表示不干预。
        """
        ...

    def on_response(self, response: str) -> str | None:
        """LLM 返回文本响应后触发。

        Returns:
            修改后的响应，或 None 表示不干预。
        """
        ...

    def on_session_end(self, messages: list[dict]) -> None:
        """对话结束时触发，可在此处做清理或记录。"""
        ...
