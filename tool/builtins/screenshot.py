"""screenshot 工具 — 截屏保存为图片文件

使用系统截图工具（多策略回退），零外部依赖。
截图保存至 .chips/screenshots/ 目录。"""

import os
import subprocess
import time

from tool.registry import registry

_SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".chips", "screenshots",
)


def _ensure_dir():
    os.makedirs(_SCREENSHOT_DIR, exist_ok=True)


def _output_path(ext: str = ".png") -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(_SCREENSHOT_DIR, f"screenshot-{ts}{ext}")


_CAPTURE_METHODS: list[tuple[str, list[str], int]] = [
    # (name, cmd_template, timeout_seconds)
    ("ImageMagick import", ["import", "-window", "root", "{path}"], 10),
    ("GNOME Screenshot", ["gnome-screenshot", "-f", "{path}"], 10),
    ("KDE Spectacle", ["spectacle", "-b", "-n", "-o", "{path}"], 10),
    ("macOS screencapture", ["screencapture", "-x", "{path}"], 10),
]


def _capture(path: str) -> str | None:
    """尝试各截图方法，返回使用的工具名或 None。"""
    for name, cmd_template, timeout in _CAPTURE_METHODS:
        cmd = [part.format(path=path) for part in cmd_template]
        try:
            subprocess.run(cmd, check=True, timeout=timeout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return name
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    return None


def handle_screenshot(args: dict) -> str:
    """截屏保存，返回文件路径。"""
    _ensure_dir()
    path = _output_path()
    tool_name = _capture(path)
    if tool_name is None:
        return ("错误：无法截屏，未找到可用的截图工具。"
                "请安装 ImageMagick (`apt install imagemagick`) 或 gnome-screenshot。")
    return f"截图已保存到 {path}（使用 {tool_name}）"


registry.register(
    name="screenshot",
    toolset="vision",
    schema={
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "屏幕截图",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    handler=handle_screenshot,
    group="dev",
    model_scope="large",
)
