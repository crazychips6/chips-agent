"""ChatScreen — Mimo-code 风格对话界面。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.screen import Screen

from agent.tui.messages import AgentChunk, ToolEvent, AgentDone
from agent.tui.worker import AgentWorker
from agent.tui.widgets.input_area import InputArea
from agent.tui.widgets.message_list import MessageList
from agent.tui.widgets.status_bar import StatusBar
from agent.tui.widgets.startup_panel import StartupPanel
from agent.tui.widgets.tool_call import ToolCall

if TYPE_CHECKING:
    from agent.loop import AIAgent
    from agent.repl import CommandRegistry

logger = logging.getLogger("chips.tui.chat")


class ChatScreen(Screen):
    """Mimo-code 风格对话界面。"""

    CSS = """
    ChatScreen {
        background: #0a0a0a;
        layout: vertical;
    }

    #messages {
        height: 1fr;
        overflow-y: auto;
        padding: 1 2;
    }

    .system-message {
        color: #808080;
        padding: 0 2;
    }

    .startup-panel {
        width: 100%;
        padding: 1 2;
        margin: 0 0 1 0;
        color: #808080;
    }

    #status {
        dock: bottom;
        height: 1;
        color: #808080;
        padding: 0 2;
    }
    """

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
        self._worker: AgentWorker | None = None
        self._current_assistant: object | None = None
        self._current_tool_card: ToolCall | None = None

    def compose(self) -> ComposeResult:
        yield MessageList(id="messages")
        yield StatusBar(id="status")
        yield InputArea(id="input-area")

    def on_mount(self) -> None:
        self._worker = AgentWorker(self.agent)

        msg_list = self.query_one("#messages", MessageList)
        msg_list.mount(StartupPanel(**self.startup_info))
        msg_list.scroll_end(animate=False)

        status = self.query_one("#status", StatusBar)
        status.model = self.startup_info.get("model", "")
        status.tool_count = self.startup_info.get("tool_count", 0)
        status.session = getattr(self.agent, "session_id", "") or ""

        self.query_one("#input-area", InputArea).focus()

    async def on_input_area_submitted(self, event: InputArea.Submitted) -> None:
        text = event.text.strip()
        if not text:
            return

        result = self.cmd_registry.dispatch(text)
        if result is not None:
            msg_list = self.query_one("#messages", MessageList)
            msg_list.append_system_message(result if result else "Done")
            return

        msg_list = self.query_one("#messages", MessageList)
        msg_list.append_user_message(text)

        if self._worker is None:
            self._worker = AgentWorker(self.agent)

        assistant_msg = msg_list.append_assistant_start()
        self._current_assistant = assistant_msg

        input_area = self.query_one("#input-area", InputArea)
        input_area._text_area.readonly = True

        try:
            await self._run_agent(text)
        finally:
            input_area._text_area.readonly = False
            input_area.focus()

    async def _run_agent(self, text: str) -> None:
        assert self._worker is not None

        def on_chunk(chunk: str) -> None:
            self.post_message(AgentChunk(chunk))

        def on_tool(name: str, args: dict, result: str | None) -> None:
            self.post_message(ToolEvent(name, args, result))

        reply = await self._worker.run(text, on_chunk=on_chunk, on_tool=on_tool)
        self.post_message(AgentDone(reply or ""))

    def on_agent_chunk(self, event: AgentChunk) -> None:
        if self._current_assistant and hasattr(self._current_assistant, "append_chunk"):
            self._current_assistant.append_chunk(event.text)

    def on_tool_event(self, event: ToolEvent) -> None:
        msg_list = self.query_one("#messages", MessageList)

        if event.result is None:
            card = ToolCall(name=event.name, args=event.args, collapsed=True)
            msg_list.mount(card)
            self._current_tool_card = card
        else:
            if self._current_tool_card is not None:
                self._current_tool_card.set_result(event.result)
                self._current_tool_card = None

        msg_list.scroll_end(animate=False)

    def on_agent_done(self, event: AgentDone) -> None:
        if self._current_assistant and hasattr(self._current_assistant, "finalize"):
            self._current_assistant.finalize()
        self._current_assistant = None
        self._current_tool_card = None

    def _show_system_message(self, text: str) -> None:
        msg_list = self.query_one("#messages", MessageList)
        msg_list.append_system_message(text)

    def _toggle_sidebar(self) -> None:
        pass
