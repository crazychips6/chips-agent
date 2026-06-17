"""统一执行日志 — 记录每次 QuickApp 调用的结果

每条日志包含：时间、工具名、参数摘要、执行时长、成功/失败、可读原因。
存储方式：.chips/quick_apps/exec_log.jsonl（追加写，JSONL 格式）。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOG_FILE = Path(".chips") / "quick_apps" / "exec_log.jsonl"
_MAX_LOG_LINES = 10000  # 最多保留行数，超限时截断旧数据


@dataclass
class LogEntry:
    timestamp: str          # ISO 格式时间
    tool_name: str           # 工具名
    args_summary: str        # 参数摘要（第一参数值，过长截断）
    duration_ms: int         # 执行时长（毫秒）
    success: bool            # 成功/失败
    reason: str = ""         # 用户可读的原因/错误说明
    args: dict[str, Any] = field(default_factory=dict)  # 原始参数（可选，用于一键重跑）


def _ensure_log_file():
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _LOG_FILE.exists():
        _LOG_FILE.write_text("", encoding="utf-8")


def record(
    tool_name: str,
    args: dict[str, Any],
    duration_ms: int,
    success: bool,
    reason: str = "",
) -> LogEntry:
    """记录一次执行日志。

    Args:
        tool_name: 工具名。
        args: 完整参数字典。
        duration_ms: 执行时长（毫秒）。
        success: 是否成功。
        reason: 用户可读的结果说明（成功时可为空，失败时必填）。
    """
    from datetime import datetime, timezone

    entry = LogEntry(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        tool_name=tool_name,
        args_summary=_summarize_args(args),
        duration_ms=duration_ms,
        success=success,
        reason=reason,
        args=args,
    )

    _ensure_log_file()
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": entry.timestamp,
                "tool_name": entry.tool_name,
                "args_summary": entry.args_summary,
                "duration_ms": entry.duration_ms,
                "success": entry.success,
                "reason": entry.reason,
                "args": entry.args,
            }, ensure_ascii=False) + "\n")

        _trim_if_needed()
    except Exception as e:
        # 日志写入失败不影响主流程
        import logging
        logging.getLogger("chips.quick_app.exec_log").warning("write_failed: %s", e)

    return entry


def get_logs(
    tool_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """读取执行日志，按时间倒序。

    Args:
        tool_name: 按工具名筛选（可选）。
        limit: 最多返回条数。
        offset: 跳过条数（用于分页）。

    Returns:
        日志条目列表（按时间倒序）。
    """
    _ensure_log_file()
    if not _LOG_FILE.exists():
        return []

    try:
        with open(_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    entries = []
    for line in reversed(lines):  # 倒序
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if tool_name and entry.get("tool_name") != tool_name:
            continue

        # 不返回原始 args 以节省带宽（前端不需要）
        clean = {k: v for k, v in entry.items() if k != "args"}
        entries.append(clean)

        if len(entries) >= offset + limit:
            break

    return entries[offset:]


def get_logs_for_tool(name: str, limit: int = 20) -> list[dict[str, Any]]:
    """获取指定工具的执行日志。"""
    return get_logs(tool_name=name, limit=limit)


def _summarize_args(args: dict[str, Any]) -> str:
    """从参数生成简短摘要供日志显示。"""
    if not args:
        return ""
    # 取第一个非空参数值作为摘要
    for key, value in args.items():
        if key.startswith("_"):
            continue
        s = str(value)
        if len(s) > 40:
            s = s[:37] + "..."
        if s:
            return s
    return str(list(args.values())[0])[:40] if args else ""


def _trim_if_needed():
    """当日志超限时截断（保留最后 _MAX_LOG_LINES 行）。"""
    try:
        lines = _LOG_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_LOG_LINES:
            trimmed = lines[-_MAX_LOG_LINES:]
            _LOG_FILE.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
    except Exception:
        pass
