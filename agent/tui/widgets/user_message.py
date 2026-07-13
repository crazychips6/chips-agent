"""UserMessage — Mimo-code 风格用户消息。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class UserMessage(Vertical):
    """Mimo-code 风格用户消息：左边框 + 面板背景。"""

    CSS = """
    UserMessage {
        width: 100%;
        margin: 0 0 1 0;
        border-left: tall #FF6A00;
    }

    .user-message-inner {
        padding: 1 2;
        background: #141414;
    }

    .user-message-text {
        color: #eeeeee;
    }
    """

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._text = text

    def compose(self) -> ComposeResult:
        with Vertical(classes="user-message-inner"):
            yield Static(self._text, classes="user-message-text")
