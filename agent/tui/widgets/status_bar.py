"""StatusBar — 底部状态栏。"""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static


class StatusBar(Static):
    """底部状态栏：模型 | 工具数 | session。"""

    model: reactive[str] = reactive("")
    tool_count: reactive[int] = reactive(0)
    session: reactive[str] = reactive("")

    def render(self) -> str:
        parts = []
        if self.model:
            parts.append(self.model)
        if self.tool_count:
            parts.append(f"{self.tool_count} tools")
        if self.session:
            parts.append(self.session[:8])
        return " │ ".join(parts) if parts else "chips"
