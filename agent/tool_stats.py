"""工具调用统计 — 记录调用次数、成功率、耗时

数据存储在内存中，支持持久化到 SQLite。
可查询：调用次数 top-N、失败率最高的工具、平均耗时最长的工具。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("chips.agent.tool_stats")


@dataclass
class ToolStat:
    """单个工具的统计数据。"""
    name: str
    call_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    total_duration_ms: int = 0
    last_called_at: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.success_count / self.call_count

    @property
    def avg_duration_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_duration_ms / self.call_count

    @property
    def fail_rate(self) -> float:
        return 1.0 - self.success_rate


class ToolStats:
    """工具调用统计管理器。"""

    def __init__(self, db_path: str | None = None):
        self._stats: dict[str, ToolStat] = {}
        self._lock = threading.Lock()
        self._db_path = db_path
        if db_path:
            self._init_db()

    def _init_db(self):
        """初始化 SQLite 数据库。"""
        try:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_stats (
                    name TEXT PRIMARY KEY,
                    call_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    fail_count INTEGER DEFAULT 0,
                    total_duration_ms INTEGER DEFAULT 0,
                    last_called_at REAL DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("tool_stats_db_init_failed: %s", e)

    def record(self, tool_name: str, success: bool, duration_ms: int):
        """记录一次工具调用。"""
        with self._lock:
            if tool_name not in self._stats:
                self._stats[tool_name] = ToolStat(name=tool_name)

            stat = self._stats[tool_name]
            stat.call_count += 1
            if success:
                stat.success_count += 1
            else:
                stat.fail_count += 1
            stat.total_duration_ms += duration_ms
            stat.last_called_at = time.time()

        # 持久化（异步，不阻塞主流程）
        if self._db_path:
            self._save_stat(tool_name)

    def _save_stat(self, tool_name: str):
        """保存单个工具统计到 SQLite。"""
        try:
            stat = self._stats.get(tool_name)
            if not stat:
                return
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                INSERT OR REPLACE INTO tool_stats
                (name, call_count, success_count, fail_count, total_duration_ms, last_called_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (stat.name, stat.call_count, stat.success_count,
                  stat.fail_count, stat.total_duration_ms, stat.last_called_at))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("tool_stats_save_failed: %s", e)

    def get(self, tool_name: str) -> ToolStat | None:
        """获取单个工具的统计数据。"""
        return self._stats.get(tool_name)

    def get_all(self) -> dict[str, ToolStat]:
        """获取所有工具的统计数据。"""
        return dict(self._stats)

    def get_top_called(self, top_n: int = 10) -> list[ToolStat]:
        """获取调用次数 top-N 的工具。"""
        stats = sorted(self._stats.values(), key=lambda s: s.call_count, reverse=True)
        return stats[:top_n]

    def get_top_failed(self, top_n: int = 10) -> list[ToolStat]:
        """获取失败率最高的工具（至少调用过 1 次）。"""
        stats = [s for s in self._stats.values() if s.call_count > 0]
        stats.sort(key=lambda s: s.fail_rate, reverse=True)
        return stats[:top_n]

    def get_top_slow(self, top_n: int = 10) -> list[ToolStat]:
        """获取平均耗时最长的工具。"""
        stats = [s for s in self._stats.values() if s.call_count > 0]
        stats.sort(key=lambda s: s.avg_duration_ms, reverse=True)
        return stats[:top_n]

    def get_summary(self) -> dict[str, Any]:
        """获取统计摘要。"""
        all_stats = list(self._stats.values())
        total_calls = sum(s.call_count for s in all_stats)
        total_fails = sum(s.fail_count for s in all_stats)
        return {
            "total_tools": len(all_stats),
            "total_calls": total_calls,
            "total_fails": total_fails,
            "overall_success_rate": (total_calls - total_fails) / total_calls if total_calls > 0 else 0,
            "top_called": [
                {"name": s.name, "calls": s.call_count, "success_rate": f"{s.success_rate:.1%}"}
                for s in self.get_top_called(5)
            ],
            "top_failed": [
                {"name": s.name, "fail_rate": f"{s.fail_rate:.1%}", "calls": s.call_count}
                for s in self.get_top_failed(5) if s.fail_count > 0
            ],
            "top_slow": [
                {"name": s.name, "avg_ms": int(s.avg_duration_ms), "calls": s.call_count}
                for s in self.get_top_slow(5)
            ],
        }

    def format_summary(self) -> str:
        """格式化统计摘要为可读字符串。"""
        summary = self.get_summary()
        lines = [
            "=== 工具调用统计 ===",
            f"总工具数: {summary['total_tools']}",
            f"总调用次数: {summary['total_calls']}",
            f"总失败次数: {summary['total_fails']}",
            f"整体成功率: {summary['overall_success_rate']:.1%}",
            "",
            "--- 调用最多 ---",
        ]
        for item in summary["top_called"]:
            lines.append(f"  {item['name']}: {item['calls']} 次 (成功率 {item['success_rate']})")

        lines.append("")
        lines.append("--- 失败率最高 ---")
        for item in summary["top_failed"]:
            lines.append(f"  {item['name']}: {item['fail_rate']} ({item['calls']} 次调用)")

        lines.append("")
        lines.append("--- 耗时最长 ---")
        for item in summary["top_slow"]:
            lines.append(f"  {item['name']}: {item['avg_ms']}ms ({item['calls']} 次调用)")

        return "\n".join(lines)


# 模块级单例（默认不持久化）
tool_stats = ToolStats()
