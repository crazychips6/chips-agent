"""Insights 引擎 — 跨会话聚合查询

终端输出使用 rich.table，Web API 输出使用 dict，同一套接口两种格式。

Usage::

    from agent.insights import InsightsEngine
    engine = InsightsEngine(session_db)
    print(engine.cost_by_model(days=7).format())
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("chips.agent.insights")


class _Result:
    """包装查询结果，支持 .format() 终端表格和 .dict() 原生数据。"""

    def __init__(self, title: str, rows: list[dict], columns: list[str]):
        self._title = title
        self._rows = rows
        self._columns = columns

    def format(self) -> str:
        """输出 rich 表格文本。"""
        if not self._rows:
            # title 可能已包含完整内容（如 portrait 的 kv table + tool table）
            if self._title:
                return f"\n{self._title}"
            return f"\n{self._title}\n（无数据）"

        # 计算列宽
        col_widths = {}
        for col in self._columns:
            header = str(col)
            max_w = len(header)
            for row in self._rows:
                val = str(row.get(col, ""))
                max_w = max(max_w, len(val))
            col_widths[col] = max_w + 2  # 两边各一个空格

        sep = "+" + "+".join("-" * w for w in col_widths.values()) + "+"

        lines = [f"\n{self._title}", sep]
        # 表头
        header_line = "|"
        for col in self._columns:
            header_line += str(col).center(col_widths[col])
        header_line += "|"
        lines.append(header_line)
        lines.append(sep)

        # 数据行
        for row in self._rows:
            row_line = "|"
            for col in self._columns:
                val = str(row.get(col, ""))
                row_line += val.ljust(col_widths[col])
            row_line += "|"
            lines.append(row_line)
        lines.append(sep)
        return "\n".join(lines)

    def dict(self) -> list[dict]:
        return self._rows


class InsightsEngine:
    """跨会话聚合查询引擎。

    所有查询方法返回 _Result 对象，同时支持终端表格和 JSON 输出。
    """

    def __init__(self, db):
        self._db = db

    def cost_by_model(self, days: int = 7) -> _Result:
        """按模型汇总费用。"""
        rows = self._db.cost_by_model(days)
        for r in rows:
            r["total_cost"] = round(r["total_cost"], 6)
        return _Result(
            f"模型费用排名（过去 {days} 天）",
            rows,
            ["model", "call_count", "prompt_tokens", "completion_tokens", "total_cost"],
        )

    def daily_cost_trend(self, days: int = 30) -> _Result:
        """每日费用趋势。"""
        rows = self._db.daily_cost_trend(days)
        for r in rows:
            r["total_cost"] = round(r["total_cost"], 6)
        return _Result(
            f"每日费用趋势（过去 {days} 天）",
            rows,
            ["day", "call_count", "prompt_tokens", "completion_tokens", "total_cost"],
        )

    def tool_usage(self, days: int = 7) -> _Result:
        """工具调用统计。"""
        rows = self._db.get_tool_call_stats(days=days)
        for r in rows:
            r["avg_duration_ms"] = round(r["avg_duration_ms"], 1) if r.get("avg_duration_ms") else 0
            total = r["call_count"]
            errs = r["error_count"]
            r["error_rate_pct"] = round(errs / total * 100, 1) if total else 0.0
        return _Result(
            f"工具使用统计（过去 {days} 天）",
            rows,
            ["tool_name", "call_count", "error_count", "error_rate_pct", "avg_duration_ms"],
        )

    def session_portrait(self, session_id: str) -> _Result:
        """单会话完整画像。"""
        data = self._db.session_portrait(session_id)
        if not data:
            return _Result("会话不存在", [], [])

        # 格式化 LLM 调用部分
        llm = data.get("llm_calls", {})
        calls = llm.get("call_count", 0)
        prompt = llm.get("prompt_tokens", 0)
        completion = llm.get("completion_tokens", 0)
        cost = llm.get("total_cost", 0)
        avg_latency = llm.get("avg_latency_ms", 0)

        summary_rows = [
            {"字段": "会话 ID", "值": data["session_id"]},
            {"字段": "标题", "值": data.get("title", "")},
            {"字段": "消息数", "值": str(data.get("msg_count", 0))},
            {"字段": "LLM 调用次数", "值": str(calls)},
            {"字段": "Prompt tokens", "值": f"{prompt:,}"},
            {"字段": "Completion tokens", "值": f"{completion:,}"},
            {"字段": "总费用", "值": f"${cost:.6f}"},
            {"字段": "平均延迟", "值": f"{avg_latency}ms"},
        ]

        # 工具调用部分
        tools = data.get("tool_calls", [])
        for t in tools:
            t["avg_duration_ms"] = round(t["avg_duration_ms"], 1) if t.get("avg_duration_ms") else 0

        header = f"\n📊 会话画像: {session_id}"
        if summary_rows:
            header += "\n" + self._kv_table(summary_rows)
        if tools:
            header += "\n\n" + _Result(
                "工具调用",
                tools,
                ["tool_name", "call_count", "error_count", "avg_duration_ms"],
            ).format()
        return _Result(header, [], [])

    def weekly_report(self) -> _Result:
        """一键周报。"""
        cost_rows = self.cost_by_model(days=7)
        tool_rows = self.tool_usage(days=7)
        daily_rows = self.daily_cost_trend(days=7)

        parts = []
        parts.append("📊 周报")
        parts.append(f"  统计周期: 过去 7 天")
        parts.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        parts.append("")

        # 总览
        total_cost = sum(r.get("total_cost", 0) for r in cost_rows.dict())
        total_calls = sum(r.get("call_count", 0) for r in cost_rows.dict())
        parts.append(f"  总费用: ${total_cost:.6f}")
        parts.append(f"  总调用: {total_calls} 次")
        parts.append("")

        parts.append(cost_rows.format())
        parts.append("")
        parts.append(tool_rows.format())

        return _Result("\n".join(parts), [], [])

    @staticmethod
    def _kv_table(rows: list[dict]) -> str:
        """渲染键值列表为表格。"""
        if not rows:
            return ""
        key_w = max(len(r.get("字段", "")) for r in rows) + 2
        val_w = max(len(str(r.get("值", ""))) for r in rows) + 2
        sep = "+" + "-" * key_w + "+" + "-" * val_w + "+"
        lines = [sep]
        for r in rows:
            k = str(r.get("字段", "")).ljust(key_w)
            v = str(r.get("值", "")).ljust(val_w)
            lines.append(f"|{k}|{v}|")
        lines.append(sep)
        return "\n".join(lines)
