"""tui_choice — 交互式选项选择器

在终端中渲染一个可通过 ↑↓ 选择、Enter 确认的选项列表。
选择完成后自动清理显示，不影响后续 TUI 渲染。
"""

from __future__ import annotations

import sys
import termios
import tty

# ANSI 颜色常量（与 agent.tui 包解耦，避免导入旧模块）
_DIM = "\033[38;2;100;100;120m"   # dim gray
_TOOL = "\033[38;2;255;200;100m"  # gold for tool calls
_RST = "\033[0m"


def pick_choice(question: str, choices: list[str] | None = None) -> str:
    """交互式单选。返回被选中的选项文本。

    choices 为 None 或空列表时退化到自由文本输入。

    显示形式:
        │ 问题文本...
        ▸ 选项1
          选项2
          选项3

    ↑↓ 切换, Enter 确认。选择后自动擦除选项列表。
    """
    # ── 自由文本模式（choices 为 None/空） ──
    if not choices:
        sys.stdout.write(f"  {_DIM}│ {question}{_RST}\n  > ")
        sys.stdout.flush()
        try:
            return input().strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    n = len(choices)
    sel = 0

    # ── 重新绘制选项行 ──

    def _rerender(s: int) -> None:
        """选中项变化时重绘所有选项行（原地覆写）。"""
        sys.stdout.write(f"\033[{n}A")
        for i, ch in enumerate(choices):
            if i == s:
                sys.stdout.write(f"\r\033[K\033[7m ▸ {ch}\033[0m\n")
            else:
                sys.stdout.write(f"\r\033[K   {ch}\n")
        sys.stdout.flush()

    # ── 显示问题 + 初始选项 ──

    try:
        sys.stdout.write(f"  {_DIM}│ {question}{_RST}\n")
        for i, ch in enumerate(choices):
            if i == 0:
                sys.stdout.write(f"\033[7m ▸ {ch}\033[0m\n")
            else:
                sys.stdout.write(f"   {ch}\n")
        sys.stdout.flush()

        # ── 原始输入循环（↑↓ 导航，Enter 确认） ──

        tty.setraw(fd)
        while True:
            b = sys.stdin.buffer.read(1)
            if b == b"\x1b":  # ESC →
                sys.stdin.buffer.read(1)  # 跳过 [
                d = sys.stdin.buffer.read(1)
                if d == b"A":  # ↑
                    if sel > 0:
                        sel -= 1
                        _rerender(sel)
                elif d == b"B":  # ↓
                    if sel < n - 1:
                        sel += 1
                        _rerender(sel)
            elif b in (b"\r", b"\n"):  # Enter
                break
            elif b == b"\x03":  # Ctrl+C
                raise KeyboardInterrupt
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    # ── 清理：擦除问题 + 选项行 ──

    sys.stdout.write(f"\033[{n + 1}A\033[J")
    sys.stdout.flush()

    return choices[sel]
