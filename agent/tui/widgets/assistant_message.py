"""AssistantMessage — Mimo-code 风格 AI 回复。"""

from __future__ import annotations

import time

from textual.widgets import Markdown


class AssistantMessage(Markdown):
    """Mimo-code 风格 AI 回复：padding-left=3。"""

    CSS = """
    AssistantMessage {
        padding-left: 3;
        margin: 1 0 0 0;
    }
    """

    _buffer: str = ""
    _last_render: float = 0.0
    _render_interval: float = 0.05

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)

    def append_chunk(self, chunk: str) -> None:
        self._buffer += chunk
        now = time.monotonic()
        if now - self._last_render >= self._render_interval:
            self._do_render()

    def _do_render(self) -> None:
        self._last_render = time.monotonic()
        self.update(self._buffer)

    def finalize(self) -> None:
        if self._buffer:
            self._do_render()

    def get_text(self) -> str:
        return self._buffer
