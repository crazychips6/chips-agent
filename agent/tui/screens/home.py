"""HomeScreen — Mimo-code 风格启动界面。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.screen import Screen
from textual.containers import Vertical
from textual.widgets import Static, Input

from agent.tui.widgets.logo import Logo
from agent.tui.widgets.starry_background import StarryBackground

if TYPE_CHECKING:
    from agent.loop import AIAgent
    from agent.repl import CommandRegistry


class HomeScreen(Screen):
    """Mimo-code 风格启动界面。"""

    CSS = """
    HomeScreen {
        background: #0a0a0a;
    }

    #starfield {
        layer: background;
        width: 100%;
        height: 100%;
    }

    #content {
        width: 100%;
        height: 100%;
        align: center top;
        padding: 0 8;
        layer: default;
    }

    #spacer-top {
        height: 6fr;
    }

    #logo-area {
        height: auto;
        width: 100%;
        align: center middle;
    }

    #logo-spacer {
        height: 1;
    }

    #input-area {
        width: 100%;
        max-width: 75;
        height: auto;
        align: center middle;
        padding-top: 1;
    }

    #home-input {
        width: 100%;
        height: 3;
        background: #1e1e1e;
        border: tall #484848;
        padding: 0 1;
    }

    #home-input:focus {
        border: tall #FF6A00;
    }

    .tip {
        color: #808080;
        text-align: center;
        width: 100%;
        margin-top: 1;
    }

    #spacer-bottom {
        height: 4fr;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
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

    def compose(self) -> ComposeResult:
        yield StarryBackground(id="starfield")
        with Vertical(id="content"):
            yield Vertical(id="spacer-top")
            with Vertical(id="logo-area"):
                yield Logo()
            yield Vertical(id="logo-spacer")
            with Vertical(id="input-area"):
                yield Input(
                    placeholder="给 chips 发消息...",
                    id="home-input",
                )
                yield Static("Ctrl+Enter 发送 | Ctrl+Q 退出", classes="tip")
            yield Vertical(id="spacer-bottom")

    def on_mount(self) -> None:
        self.query_one("#home-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        from agent.tui.screens.chat import ChatScreen
        self.app.push_screen(ChatScreen(
            agent=self.agent,
            cmd_registry=self.cmd_registry,
            startup_info=self.startup_info,
        ))

    def action_quit(self) -> None:
        self.app.exit()
