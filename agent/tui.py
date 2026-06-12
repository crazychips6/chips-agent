"""TUI — 终端用户界面

职责：
  1. 启动信息面板
  2. 对话流式输出（Markdown 渲染）
  3. 工具调用提示（规划中）
  4. 用量统计显示

使用方式：
  tui = TUI()
  tui.startup(model="...", tool_count=12, ...)
  reply = tui.chat(agent, "你好")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from agent.loop import AIAgent

logger = logging.getLogger("chips.tui")

ENABLE_RICH = True
try:
    from rich.console import Console as _RichConsole
    from rich.live import Live as _RichLive
    from rich.markdown import Markdown as _RichMarkdown
    from rich.panel import Panel as _RichPanel
    from rich.rule import Rule as _RichRule
    from rich.text import Text as _RichText
except ImportError:
    ENABLE_RICH = False


@dataclass
class _StreamState:
    """流式输出的累计状态"""
    buffer: str = ""
    final_reply: str = ""
    live: object = None  # rich.live.Live 实例，仅 rich 模式使用
    on_first_chunk: Callable | None = None
    _first: bool = True


class TUI:
    """终端用户界面 —— 统一管理所有终端输出。"""

    def __init__(self):
        self._rich = ENABLE_RICH
        if self._rich:
            self._console = _RichConsole()
        self._current_chat: _StreamState | None = None

    # ── 对外接口 ──

    def startup(
        self,
        *,
        model: str,
        tool_count: int,
        toolset_names: list[str],
        memory_status: str = "off",
        mcp_status: str = "off",
        skill_status: str = "off",
        compress_status: str = "on",
        context_file_count: int = 0,
    ) -> None:
        """显示启动信息面板。"""
        if self._rich:
            self._print_startup_panel(
                model=model,
                tool_count=tool_count,
                toolset_names=toolset_names,
                memory_status=memory_status,
                mcp_status=mcp_status,
                skill_status=skill_status,
                compress_status=compress_status,
                context_file_count=context_file_count,
            )
        else:
            self._print_startup_plain(
                model=model,
                tool_count=tool_count,
                toolset_names=toolset_names,
                memory_status=memory_status,
                mcp_status=mcp_status,
                skill_status=skill_status,
                compress_status=compress_status,
                context_file_count=context_file_count,
            )

    def chat(self, agent: AIAgent, text: str, max_iterations: int = 20) -> str:
        """对话单轮：展示用户消息 → 流式输出助理回复 → 返回完整文本。"""
        state = _StreamState()
        self._current_chat = state

        # 展示用户消息
        self._show_user_message(text)

        # 流式回调 —— 由 agent.run_conversation 的 chunk_callback 调用
        def _on_chunk(chunk: str):
            if state._first:
                state._first = False
                self._begin_assistant(state)
            state.buffer += chunk
            self._update_assistant(state)

        # 启动 Live（rich 模式）
        if self._rich:
            state.live = _RichLive(
                _RichPanel("", title="🤖 chips"),
                refresh_per_second=12,
                transient=False,
            )
            state.live.__enter__()

        try:
            reply = agent.run_conversation(
                text,
                max_iterations=max_iterations,
                chunk_callback=_on_chunk,
            )
        finally:
            if state.live is not None:
                state.live.__exit__(None, None, None)

        # 最终呈现
        final = reply or state.buffer
        state.final_reply = final
        is_streaming = not state._first and not reply
        if self._rich and final:
            # rich 模式：Live 退出后渲染最终 Markdown Panel
            self._console.print(_RichPanel(
                _RichMarkdown(final),
                title="🤖 chips",
                border_style="green",
            ))
        elif not is_streaming and final:
            # 非流式纯文本：打印完整回复
            print(final)
        # 流式纯文本：已逐 token 输出，不再重复打印

        self._current_chat = None
        return final

    # ── 内部方法 ──

    def _show_user_message(self, text: str) -> None:
        """在对话流中展示用户消息。"""
        if self._rich:
            self._console.print()
            self._console.print(_RichRule(style="dim white"))
            self._console.print(f"[bold]你[/]  {text}")
            self._console.print()
        else:
            print()
            print(f"你  {text}")
            print()

    def _begin_assistant(self, state: _StreamState) -> None:
        """流式开始前的准备工作（rich 模式下由 Live 接管）。"""
        if not self._rich:
            print("🤖 chips  ", end="", flush=True)

    def _update_assistant(self, state: _StreamState) -> None:
        """流式进行中：刷新显示。"""
        if state.live is not None:
            text = state.buffer
            # 短内容用纯文本，累积到一定长度后渲染 Markdown
            if len(text) > 80:
                state.live.update(_RichPanel(
                    _RichMarkdown(text),
                    title="🤖 chips",
                ))
            else:
                state.live.update(_RichPanel(
                    text,
                    title="🤖 chips",
                ))
        else:
            # 纯文本模式：逐 token 打印
            print(state.buffer[-1:], end="", flush=True)

    # ── startup 的具体实现 ──

    def _print_startup_panel(self, **kw):
        from rich.panel import Panel
        model = kw["model"]
        tool_count = kw["tool_count"]
        toolset_str = ", ".join(
            f"{n}✓" for n in kw["toolset_names"]
        ) if kw["toolset_names"] else "core"
        lines = [
            f"[bold]Model:[/] {model}",
            f"[bold]Tools:[/] {tool_count} ({toolset_str})",
        ]
        parts = []
        if kw["memory_status"] != "off":
            parts.append(f"Mem: {kw['memory_status']}")
        if kw["mcp_status"] != "off":
            parts.append(f"MCP: {kw['mcp_status']}")
        if kw["skill_status"] != "off":
            parts.append(f"Skills: {kw['skill_status']}")
        if kw["compress_status"] != "off":
            parts.append(f"Compress: {kw['compress_status']}")
        if kw["context_file_count"]:
            parts.append(f"Files: {kw['context_file_count']}")
        if parts:
            lines.append(f"[dim]{'  |  '.join(parts)}[/]")
        self._console.print(Panel(
            "\n".join(lines),
            title="[bold cyan]chips[/]",
            border_style="cyan",
        ))

    def _print_startup_plain(self, **kw):
        print(f"chips v0.3.0 — model: {kw['model']}")
        ts = ", ".join(kw["toolset_names"])
        print(f"tools: {kw['tool_count']} ({ts})  |  memory: {kw['memory_status']}")
        parts = []
        if kw["mcp_status"] != "off":
            parts.append(f"mcp: {kw['mcp_status']}")
        if kw["skill_status"] != "off":
            parts.append(f"skills: {kw['skill_status']}")
        if parts:
            print(" | ".join(parts))
