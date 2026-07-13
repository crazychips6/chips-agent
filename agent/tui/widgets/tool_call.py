"""ToolCall — Mimo-code 风格工具调用。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static


def _json_preview(args: dict) -> str:
    """精简显示工具参数。"""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


# Mimo-code 工具图标映射
TOOL_ICONS: dict[str, str] = {
    "bash": "$",
    "read": "→",
    "write": "←",
    "glob": "✱",
    "grep": "✱",
    "webfetch": "%",
    "websearch": "◈",
    "codesearch": "◇",
}


class ToolCall(Vertical):
    """Mimo-code 风格工具调用。"""

    CSS = """
    ToolCall {
        padding-left: 3;
        margin: 1 0 0 0;
    }

    .tool-header {
        color: #808080;
    }

    .tool-result {
        color: #808080;
    }
    """

    BINDINGS = [
        Binding("enter", "toggle", "展开/折叠"),
        Binding("space", "toggle", "展开/折叠"),
    ]

    collapsed = reactive(True)

    def __init__(self, name: str, args: dict, collapsed: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self._name = name
        self._args = args
        self._result: str | None = None
        self.collapsed = collapsed

    def compose(self) -> ComposeResult:
        icon = TOOL_ICONS.get(self._name, "⚙")
        args_str = _json_preview(self._args)
        yield Static(f"{icon} {self._name}({args_str})", classes="tool-header")
        if not self.collapsed and self._result is not None:
            preview = "\n".join(self._result.split("\n")[:10])
            yield Static(f"│ {preview}", classes="tool-result")

    def set_result(self, result: str) -> None:
        self._result = result
        self.collapsed = False
        self.refresh()

    def action_toggle(self) -> None:
        self.collapsed = not self.collapsed
        self.refresh()
