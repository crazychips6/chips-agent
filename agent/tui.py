"""TUI — 终端用户界面

对话展示风格（无边框，分区展示）：
  ───────────────────────────────────

  你
  上海天气

  chips
  好的，我来查一下...

  🛠 web_search("上海 天气")
     搜索结果: ...

  chips
  上海明天天气：晴转多云，28°C/22°C
"""

from __future__ import annotations

import logging
import re
import shutil
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from agent.loop import AIAgent

logger = logging.getLogger("chips.tui")

# ── ANSI 颜色 ──

_ACCENT = "\033[1;38;2;100;255;218m"  # cyan bold
_DIM = "\033[38;2;100;100;120m"        # dim gray
_TOOL = "\033[38;2;255;200;100m"       # gold for tool calls
_RST = "\033[0m"
_ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def _vis_len(text: str) -> int:
    return len(_ANSI_RE.sub('', text))


def _cprint(text: str) -> None:
    try:
        from prompt_toolkit import print_formatted_text as _pt_print
        from prompt_toolkit.formatted_text import ANSI as _PT_ANSI
        _pt_print(_PT_ANSI(text))
    except ImportError:
        print(text)


@dataclass
class _StreamState:
    buffer: str = ""
    started: bool = False
    final_reply: str = ""


class TUI:
    def __init__(self):
        self._state: _StreamState | None = None
        try:
            from rich.console import Console
            from rich.markdown import Markdown as _RichMarkdown
            from rich.panel import Panel as _RichPanel
            self._rich = True
            self._console = Console(highlight=False)
            self._RichMarkdown = _RichMarkdown
            self._RichPanel = _RichPanel
        except ImportError:
            self._rich = False

    # ── 启动面板 ──

    def startup(self, *, model: str, tool_count: int, toolset_names: list[str],
                memory_status: str = "off", mcp_status: str = "off",
                skill_status: str = "off", compress_status: str = "on",
                context_file_count: int = 0) -> None:
        _banner = [
            "╔═╗ ╦ ╦ ╦ ╔═╗ ╔═╗",
            "║   ╠═╣ ║ ╠═╣ ╚═╗",
            "╚═╝ ╩ ╩ ╩ ╩   ╚═╝",
        ]
        w = shutil.get_terminal_size().columns

        # 顶边框（带 logo）
        prefix = "╭─ "
        b1_vis = _vis_len(_banner[0])
        fill = w - len(prefix) - b1_vis - 1
        _cprint(f"\n{_ACCENT}{prefix}{_banner[0]}{'─' * max(fill, 0)}╮{_RST}")
        for _b in _banner[1:]:
            self._box_line(_b, w)
        self._box_line("", w)

        # 信息行
        ts = ", ".join(f"{n}✓" for n in toolset_names) if toolset_names else "core"
        self._box_line(f"Model: {model}  |  Tools: {tool_count} ({ts})", w)
        parts = []
        if memory_status != "off":
            parts.append(f"Mem: {memory_status}")
        if mcp_status != "off":
            parts.append(f"MCP: {mcp_status}")
        if skill_status != "off":
            parts.append(f"Skills: {skill_status}")
        if compress_status != "off":
            parts.append(f"Compress: {compress_status}")
        if context_file_count:
            parts.append(f"Files: {context_file_count}")
        if parts:
            self._box_line("  |  ".join(parts), w, dim=True)
        _cprint(f"{_ACCENT}╰{'─' * (w - 2)}╯{_RST}")

    def _box_line(self, text: str, w: int, dim: bool = False) -> None:
        prefix = _DIM if dim else ""
        visible = _vis_len(text)
        pad = w - 3 - visible
        _cprint(f"{_ACCENT}│{_RST} {prefix}{text}{_RST}{' ' * max(pad, 1)}{_ACCENT}│{_RST}")

    # ── 对话 ──

    def chat(self, agent: AIAgent, text: str, max_iterations: int = 20) -> str:
        state = _StreamState()
        self._state = state

        self._show_user(text)

        def _on_chunk(chunk: str):
            if not state.started:
                state.started = True
                _cprint(f"\n{_ACCENT} chips{_RST}")
                sys.stdout.write("  ")
            state.buffer += chunk
            sys.stdout.write(chunk)
            sys.stdout.flush()

        def _on_tool(name: str, args: dict, result: str | None):
            if result is None:
                # 工具开始调用
                args_preview = json_preview(args)
                _cprint(f"\n{_TOOL}  🛠 {name}({args_preview}){_RST}")
            else:
                # 工具返回结果（精简显示）
                preview = result[:200].replace("\n", " ")
                _cprint(f"{_DIM}     {preview}{_RST}")

        try:
            reply = agent.run_conversation(
                text, max_iterations=max_iterations,
                chunk_callback=_on_chunk,
                tool_callback=_on_tool,
            )
        finally:
            sys.stdout.write("\n")
            sys.stdout.flush()

        final = reply or state.buffer
        state.final_reply = final

        if not state.started and final:
            # 非流式模式
            _cprint(f"\n{_ACCENT} chips{_RST}")
            _cprint(f" {final}")

        self._state = None
        return final

    # ── 用户消息 ──

    def _show_user(self, text: str) -> None:
        w = shutil.get_terminal_size().columns
        _cprint(f"\n{_DIM}{'─' * w}{_RST}")
        _cprint(f"  {_ACCENT}你{_RST}")
        _cprint(f"  {text}")

def json_preview(args: dict) -> str:
    """精简显示工具参数，避免过长的字符串。"""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)
