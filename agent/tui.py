"""TUI — 终端用户界面

渲染风格（圆环聚焦 + 层级缩进）：
  ● 你
    你好，今天天气怎么样？

  ○ chips
    我来查一下天气...

    ○ 🛠 web_search(query="上海 天气")
       │ 上海今天晴，22-28°C

    上海今天晴转多云，22～28°C。
"""

from __future__ import annotations

import logging
import re
import shutil
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

try:
    from rich.console import Console
    from rich.syntax import Syntax
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False

if TYPE_CHECKING:
    from agent.loop import AIAgent

logger = logging.getLogger("chips.tui")

# ── ANSI 颜色 ──

_ACCENT = "\033[1;38;2;100;255;218m"  # cyan bold
_DIM = "\033[38;2;100;100;120m"        # dim gray
_TOOL = "\033[38;2;255;200;100m"       # gold for tool calls
_USER_ARROW = "\033[38;2;180;140;60m"  # 深黄色 >（用户消息行前缀）
_RST = "\033[0m"

# ── 圆环符号 ──
_SOLID = "●"               # U+25CF  非聚焦段落

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


# ── 文本折行 ──

def _wrap_line(line: str, indent: int, width: int | None = None) -> list[str]:
    """将一行文本按终端宽度折行，续行保持 indent 缩进。

    Returns:
        物理行列表（每行不含尾换行符）。
    """
    w = width or shutil.get_terminal_size().columns
    margin = " " * indent
    if not line:
        return [margin]
    result: list[str] = []
    first = True
    while line:
        prefix = margin
        avail = w - indent
        if avail <= 0:
            break
        chunk = line[:avail]
        # 非首行且长度正好超限时尝试在空格处折行
        if not first and len(chunk) == avail and len(line) > avail:
            break_at = chunk.rfind(" ")
            if break_at > 0:
                chunk = chunk[:break_at]
                line = line[break_at:].lstrip()
            else:
                line = line[avail:].lstrip()
        else:
            line = line[avail:]
        result.append(f"{prefix}{chunk}")
        first = False
    return result if result else [margin]


def _wrap_text(text: str, indent: int = 2, width: int | None = None) -> str:
    """将多行文本折行，每行缩进 indent 格。"""
    lines: list[str] = []
    for para in text.split("\n"):
        wrapped = _wrap_line(para, indent, width)
        if wrapped:
            lines.extend(wrapped)
        else:
            lines.append(" " * indent)
    return "\n".join(lines)


# ── 聚焦追踪 ──

@dataclass
class _FocusNode:
    """聚焦段落节点。"""
    indent: int      # ●/○ 所在列
    height: int      # 本段落占用的物理行数（含 ●/○ 行）


class FocusTracker:
    """管理 ●/○ 的状态切换与 ANSI blink 覆盖。

    原理：
      - 新聚焦段落开始时，栈顶节点取消 blink（变实心），新节点带 blink
      - 段落结束时，该节点变实心，若存在父节点则恢复 blink
      - 通过 ANSI 光标移动定位行首，覆写首列字符
    """

    def __init__(self):
        self._stack: list[_FocusNode] = []
        self._cursor_height = 0  # 当前节点累计的物理行数

    # ── 段落开始 ──

    def begin(self, indent: int, inline: str = "") -> None:
        """开始一个新段落。直接用 ●，不做空心→实心动画。"""
        margin = " " * indent
        sys.stdout.write(f"{margin}{_SOLID}{inline}")
        sys.stdout.flush()
        self._stack.append(_FocusNode(indent=indent, height=1))
        self._cursor_height = 1

    # ── 添加内容行 ──

    def println(self, text: str = "", indent: int | None = None) -> None:
        """在当前段落中添加一行内容（自动按终端宽度折行）。

        Args:
            text: 内容文本
            indent: 缩进格数，默认与当前段落 indent + 2
        """
        if not self._stack:
            # 没有活跃段落时直接 print（备用路径）
            if text:
                print(text)
            else:
                print()
            return

        node = self._stack[-1]
        ind = indent if indent is not None else node.indent + 2
        lines = _wrap_line(text, ind)
        for l in lines:
            print(l)
            node.height += 1
            self._cursor_height += 1

    def writeln(self, text: str = "", indent: int | None = None) -> None:
        """println 的别名。"""
        self.println(text, indent)

    # ── 段落结束 ──

    def end(self) -> None:
        """结束当前段落。直接弹栈，无需覆写。"""
        if self._stack:
            self._stack.pop()

    # ── 异常清理 ──

    def clear(self) -> None:
        """清理所有未关闭的段落。"""
        while self._stack:
            self.end()


# ── 工具参数预览 ──

def json_preview(args: dict) -> str:
    """精简显示工具参数，避免过长的字符串。"""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


# ── TUI 主类 ──

@dataclass
class _StreamState:
    buffer: str = ""
    started: bool = False
    final_reply: str = ""


class TUI:
    def __init__(self):
        self._state: _StreamState | None = None
        self._focus = FocusTracker()
        try:
            from rich.console import Console
            self._rich = True
            self._console = Console(highlight=False)
        except ImportError:
            self._rich = False

    # ── 启动面板 ──

    def startup(self, *, model: str, tool_count: int, toolset_names: list[str],
                memory_status: str = "off", mcp_status: str = "off",
                skill_status: str = "off", compress_status: str = "on",
                context_file_count: int = 0,
                fast_llm_status: str = "off") -> None:
        _banner = [
            "╔═╗ ╦ ╦ ╦ ╔═╗ ╔═╗ ",
            " ║   ╠═╣ ║ ╠═╣ ╚═╗",
            " ╚═╝ ╩ ╩ ╩ ╩   ╚═╝",
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
        if fast_llm_status != "off":
            parts.append(f"Fast: {fast_llm_status}")
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

    def chat(self, agent: AIAgent, text: str, max_iterations: int = 20,
             clear_prompt: bool = False) -> str:
        state = _StreamState()
        self._state = state

        # 用户消息段落（已完成 → ●）
        self._show_user(text, clear_prompt=clear_prompt)

        def _on_chunk(chunk: str):
            if not state.started:
                state.started = True
                sys.stdout.write("\n")
                # 直接用 ●（不做 ○→● 覆写，行数追踪不可靠）
                sys.stdout.write(f" {_ACCENT}● chips{_RST}\n")
            state.buffer += chunk
            sys.stdout.write(chunk)
            sys.stdout.flush()

        def _on_agent(agent_name: str, task: str, result: str | None, record_id: str | None):
            """子 Agent 生命周期回调（不输出细节）。"""

        def _on_tool(name: str, args: dict, result: str | None):
            # ── clarify 工具：不走 FocusTracker（由 ChoicePicker 管理） ──
            if name == "clarify":
                if result is None:
                    sys.stdout.write("\n")
                    args_preview = json_preview(args)
                    sys.stdout.write(f"  {_TOOL}🛠 clarify({args_preview}){_RST}\n")
                    sys.stdout.flush()
                else:
                    try:
                        import json
                        data = json.loads(result)
                        answer = data.get("user_response", "")
                    except Exception:
                        answer = str(result)[:100]
                    sys.stdout.write(f"  {_DIM}│ {answer}{_RST}\n")
                    sys.stdout.flush()
                return

            # ── 其他工具：正常 FocusTracker 流程 ──
            if result is None:
                sys.stdout.write("\n")
                args_preview = json_preview(args)
                self._focus.begin(2, f" {_TOOL}🛠 {name}({args_preview}){_RST}")
                sys.stdout.write("\n")
                sys.stdout.flush()
            else:
                lines = result.split("\n")[:5]
                if "```" in lines[0] and _RICH_AVAILABLE:
                    lang = lines[0].removeprefix("```").strip()
                    code = "\n".join(l for l in lines[1:] if not l.strip().startswith("```"))
                    try:
                        from rich.syntax import Syntax
                        from rich.console import Console
                        import io
                        buf = io.StringIO()
                        Console(file=buf, width=shutil.get_terminal_size().columns - 6).print(
                            Syntax(code, lang or "text", theme="monokai", background_color="default",
                                   line_numbers=False, word_wrap=True)
                        )
                        preview = buf.getvalue().rstrip()
                        for pl in preview.split("\n"):
                            self._focus.writeln(f"{_DIM}{pl}{_RST}", indent=4)
                    except Exception:
                        for line in lines:
                            self._focus.writeln(f"{_DIM}│ {line}{_RST}", indent=4)
                else:
                    for line in lines:
                        self._focus.writeln(f"{_DIM}│ {line}{_RST}", indent=4)
                self._focus.end()

        try:
            reply = agent.run_conversation(
                text, max_iterations=max_iterations,
                chunk_callback=_on_chunk,
                tool_callback=_on_tool,
                agent_callback=_on_agent,
            )
        finally:
            sys.stdout.write("\n")
            sys.stdout.flush()

        sys.stdout.write("\n")  # 与下一个输入之间保留空行

        final = reply or state.buffer
        state.final_reply = final

        if not state.started and final:
            # 非流式模式 — 补打 chips 段落
            sys.stdout.write(f" {_ACCENT}● chips{_RST}\n")
            for line_text in _wrap_text(final.strip(), indent=2).split("\n"):
                print(line_text)
            print()

        # 安全清理
        self._focus.clear()
        self._state = None
        return final

    # ── 用户消息 ──

    def _show_user(self, text: str, clear_prompt: bool = False) -> None:
        """渲染用户消息段落。

        Args:
            text: 用户输入文本
            clear_prompt: 是否先清除上一行（prompt_toolkit 的输入提示）
        """
        if clear_prompt:
            # 上移一行 + 清除整行，消除 prompt_toolkit 遗留的 "❯ 你好" 行
            sys.stdout.write("\033[1A\033[2K\r")

        # 用户消息：整行深黄色（> 前缀 + 内容）
        for line_text in _wrap_text(text.strip(), indent=0).split("\n"):
            print(f"{_USER_ARROW}> {line_text}{_RST}")

        print()
