"""ChipsApp — Mimo-code 风格主应用。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App
from textual.binding import Binding

from agent.tui.screens.home import HomeScreen

if TYPE_CHECKING:
    from agent.loop import AIAgent
    from agent.repl import CommandRegistry


class ChipsApp(App):
    """Mimo-code 风格 TUI 主应用。"""

    TITLE = "chips"

    CSS = """
    Screen {
        background: #0a0a0a;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出", show=True),
    ]

    def __init__(
        self,
        agent: AIAgent,
        cmd_registry: CommandRegistry,
        startup_info: dict,
    ) -> None:
        super().__init__()
        self.agent = agent
        self.cmd_registry = cmd_registry
        self.startup_info = startup_info

    def on_mount(self) -> None:
        self.push_screen(HomeScreen(
            agent=self.agent,
            cmd_registry=self.cmd_registry,
            startup_info=self.startup_info,
        ))

    def action_quit(self) -> None:
        self.exit()
