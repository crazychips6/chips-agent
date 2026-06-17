"""详细追踪日志 — 记录每次检索和创建的完整过程

写入 .chips/quick_apps/trace.log，开发者可直接 tail -f 查看。
每个请求用 ==== 分隔，包含时间戳和完整过程。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_LOG_FILE = Path(".chips") / "quick_apps" / "trace.log"
_MAX_SIZE = 5 * 1024 * 1024  # 5MB


def _ensure_file():
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _LOG_FILE.exists():
        _LOG_FILE.write_text("", encoding="utf-8")


def _trim():
    """超限时截断前半。"""
    try:
        if _LOG_FILE.stat().st_size > _MAX_SIZE:
            content = _LOG_FILE.read_text(encoding="utf-8")
            half = len(content) // 2
            _LOG_FILE.write_text(content[half:].lstrip("\n"), encoding="utf-8")
    except Exception:
        pass


def write(entry_type: str, title: str, data: dict | str):
    """写入一条追踪日志。"""
    _ensure_file()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(data, str):
        data_str = data
    else:
        try:
            data_str = json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            data_str = str(data)

    line = (
        f"===== {ts} [{entry_type}] {title} =====\n"
        f"{data_str}\n\n"
    )
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        _trim()
    except Exception:
        pass
