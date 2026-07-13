"""MessageList — 消息列表容器。"""

from __future__ import annotations

from textual.containers import VerticalScroll

from agent.tui.widgets.user_message import UserMessage
from agent.tui.widgets.assistant_message import AssistantMessage


class MessageList(VerticalScroll):
    """消息列表容器，自动滚动到底部。"""

    def append_user_message(self, text: str) -> None:
        """添加用户消息。"""
        self.mount(UserMessage(text))
        self.scroll_end(animate=False)

    def append_assistant_start(self) -> AssistantMessage:
        """创建 AI 回复 widget 并挂载。"""
        widget = AssistantMessage(classes="assistant-message")
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget

    def append_system_message(self, text: str) -> None:
        """添加系统消息。"""
        from textual.widgets import Static
        self.mount(Static(f"  {text}", classes="system-message"))
        self.scroll_end(animate=False)
