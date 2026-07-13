"""InputArea — Mimo-code 风格输入框。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import TextArea, Static


class InputArea(Vertical):
    """Mimo-code 风格输入框。"""

    CSS = """
    InputArea {
        height: auto;
        min-height: 3;
        max-height: 12;
        margin: 0 2;
        border-left: tall #484848;
        background: #1e1e1e;
    }

    InputArea:focus-within {
        border-left: tall #FF6A00;
    }

    #input-text {
        height: auto;
        min-height: 2;
        background: #1e1e1e;
    }

    .input-hint {
        dock: bottom;
        height: 1;
        color: #808080;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+enter", "submit", "发送"),
    ]

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._text_area = TextArea(id="input-text")

    def compose(self) -> ComposeResult:
        yield self._text_area
        yield Static("Ctrl+Enter 发送", classes="input-hint")

    def action_submit(self) -> None:
        text = self._text_area.text.strip()
        if text:
            self.post_message(self.Submitted(text))
            self._text_area.text = ""

    @property
    def text(self) -> str:
        return self._text_area.text

    def focus(self, **kwargs) -> None:
        self._text_area.focus(**kwargs)
