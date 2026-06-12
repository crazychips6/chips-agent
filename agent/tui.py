"""TUI — 终端用户界面

仿 Hermes CLI 风格：
  - prompt_toolkit ANSI 渲染（`_cprint`），避免 rich.Live 的线程问题
  - 行缓冲流式输出，unicode 边框符绘制对话气泡
  - rich 仅用于最终回复 Markdown 渲染（非流式模式）

使用方式：
  tui = TUI()
  tui.startup(model="...", tool_count=12, ...)
  reply = tui.chat(agent, "你好")
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from agent.loop import AIAgent

logger = logging.getLogger("chips.tui")

# ── ANSI 颜色常量 ──

_ACCENT = "\033[1;38;2;100;255;218m"  # cyan bold (chips accent)
_DIM = "\033[38;2;100;100;120m"       # dim gray
_RST = "\033[0m"                       # reset

# ── prompt_toolkit ANSI 渲染 ──

try:
    from prompt_toolkit import print_formatted_text as _pt_print
    from prompt_toolkit.formatted_text import ANSI as _PT_ANSI
    _HAS_PT = True
except ImportError:
    _HAS_PT = False


def _cprint(text: str) -> None:
    """通过 prompt_toolkit 渲染 ANSI 彩色文本（兼容 patch_stdout）。"""
    if _HAS_PT:
        _pt_print(_PT_ANSI(text))
    else:
        print(text)


# ── rich（可选） ──

ENABLE_RICH = True
try:
    from rich.markdown import Markdown as _RichMarkdown
    from rich.panel import Panel as _RichPanel
except ImportError:
    ENABLE_RICH = False


# ── 流式输出状态 ──


@dataclass
class _StreamState:
    """每次 chat() 调用的内部状态。"""
    buffer: str = ""            # 完整累计内容
    stream_buf: str = ""        # 行缓冲：未刷新的部分行
    box_opened: bool = False    # 是否已打开回复框
    started: bool = False       # 是否收到首个 chunk
    final_markdown: str = ""    # 最终要渲染的 Markdown（仅在 rich 模式使用）


# ── TUI 主类 ──


class TUI:
    """终端用户界面 —— 统一管理所有终端输出。"""

    def __init__(self):
        self._rich = ENABLE_RICH
        if self._rich:
            from rich.console import Console
            self._console = Console(highlight=False)
        self._state: _StreamState | None = None

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
        """显示启动信息面板（ANSI 盒子风格）。"""
        self._print_startup_panel(
            model=model, tool_count=tool_count, toolset_names=toolset_names,
            memory_status=memory_status, mcp_status=mcp_status,
            skill_status=skill_status, compress_status=compress_status,
            context_file_count=context_file_count,
        )

    def chat(self, agent: AIAgent, text: str, max_iterations: int = 20) -> str:
        """对话单轮：展示用户消息 → 流式输出助理回复 → 返回完整文本。"""
        state = _StreamState()
        self._state = state

        self._show_user_message(text)

        def _on_chunk(chunk: str):
            if not state.started:
                state.started = True
                self._open_response_box(state)
            state.buffer += chunk
            self._stream_chunk(state, chunk)

        try:
            reply = agent.run_conversation(
                text,
                max_iterations=max_iterations,
                chunk_callback=_on_chunk,
            )
        finally:
            if state.box_opened:
                self._close_response_box(state)

        final = reply or state.buffer
        state.final_markdown = final
        is_streaming = state.started and not reply

        # 非流式模式：打印完整回复
        if not is_streaming and final:
            if self._rich:
                self._console.print(_RichPanel(
                    _RichMarkdown(final),
                    title="🤖 chips",
                    border_style="green",
                ))
            else:
                print(final)

        self._state = None
        return final

    # ── 用户消息展示 ──

    def _show_user_message(self, text: str) -> None:
        """在对话流中展示用户消息。"""
        w = shutil.get_terminal_size().columns
        _cprint(f"\n{_DIM}{'─' * w}{_RST}")
        _cprint(f"{_ACCENT}●{_RST} {text}")

    # ── 回复框 ──

    def _open_response_box(self, state: _StreamState) -> None:
        """打开回复框的顶边框。"""
        w = shutil.get_terminal_size().columns
        label = "🤖 chips"
        fill = w - 2 - len(label)
        _cprint(f"\n{_ACCENT}╭─{label}{'─' * max(fill - 1, 0)}╮{_RST}")
        state.box_opened = True

    def _close_response_box(self, state: _StreamState) -> None:
        """关闭回复框的底边框 + 刷新行缓冲。"""
        if state.stream_buf:
            _cprint(f"    {state.stream_buf}")
            state.stream_buf = ""
        w = shutil.get_terminal_size().columns
        _cprint(f"{_ACCENT}╰{'─' * (w - 2)}╯{_RST}")

    # ── 流式输出（行缓冲） ──

    def _stream_chunk(self, state: _StreamState, chunk: str) -> None:
        """行缓冲流式输出：将文本按行分割，完整行立即刷新。"""
        state.stream_buf += chunk
        while "\n" in state.stream_buf:
            line, state.stream_buf = state.stream_buf.split("\n", 1)
            _cprint(f"    {line}")

    # ── 启动面板 ──

    def _print_startup_panel(self, **kw):
        """ANSI 盒子绘制启动信息（仿 Hermes 风格）。"""
        w = shutil.get_terminal_size().columns
        model = kw["model"]
        tool_count = kw["tool_count"]
        toolset_str = ", ".join(
            f"{n}✓" for n in kw["toolset_names"]
        ) if kw["toolset_names"] else "core"
        footer_parts = []
        if kw["memory_status"] != "off":
            footer_parts.append(f"Mem: {kw['memory_status']}")
        if kw["mcp_status"] != "off":
            footer_parts.append(f"MCP: {kw['mcp_status']}")
        if kw["skill_status"] != "off":
            footer_parts.append(f"Skills: {kw['skill_status']}")
        if kw["compress_status"] != "off":
            footer_parts.append(f"Compress: {kw['compress_status']}")
        if kw["context_file_count"]:
            footer_parts.append(f"Files: {kw['context_file_count']}")

        label = " chips "
        _cprint(f"\n{_ACCENT}╭─{label}{'─' * (w - 5 - len(label))}╮{_RST}")
        self._box_line(f"Model: {model}  |  Tools: {tool_count} ({toolset_str})", w)
        if footer_parts:
            self._box_line(f"{'  |  '.join(footer_parts)}", w, dim=True)
        _cprint(f"{_ACCENT}╰{'─' * (w - 2)}╯{_RST}")

    def _box_line(self, text: str, width: int, dim: bool = False) -> None:
        """打印盒子内的一行文字（带两侧边框）。"""
        prefix = _DIM if dim else ""
        content = f"  {text}"
        pad = width - 4 - len(text)  # │ + space + text + space + │
        _cprint(f"{_ACCENT}│{_RST} {prefix}{text}{_RST}{' ' * max(pad, 1)}{_ACCENT}│{_RST}")

    def _print_startup_plain(self, **kw):
        """纯文本回退（无 prompt_toolkit 时）。"""
        print(f"chips v0.3.0 — model: {kw['model']}")
        print(f"tools: {kw['tool_count']} ({', '.join(kw['toolset_names'])})  |  memory: {kw['memory_status']}")
