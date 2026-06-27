"""/rewind 命令 — 回退 N 轮对话

通过 factory function make_handler(agent) 注入依赖。
"""

from __future__ import annotations

from typing import Any, Callable


def make_handler(agent: Any) -> Callable[[list[str]], str | None]:
    """创建 /rewind 命令处理器。

    Args:
        agent: AIAgent 实例

    Returns:
        handler(args) -> str | None
    """

    def handler(args: list[str]) -> str | None:
        """将对话回退到之前的状态。"""
        n = 1
        if args:
            try:
                n = int(args[0])
                if n < 1:
                    return "⚠ 回退轮数必须 >= 1"
            except ValueError:
                return f"⚠ 无效参数：{args[0]}，用法：/rewind [轮数]"

        if not agent.messages:
            return "对话已为空，无法回退"

        # 从尾部向前找第 N 个 user 消息的索引
        user_indices = [i for i, m in enumerate(agent.messages) if m.get("role") == "user"]
        if len(user_indices) <= 1:
            return "⚠ 历史不足，无法回退（至少需要保留一个 user 消息）"
        if n >= len(user_indices):
            return f"⚠ 对话历史不足（当前共 {len(user_indices) - 1} 轮可回退）"

        cut_at = user_indices[-n]
        removed = len(agent.messages) - cut_at

        # 截断消息（保留 cut_at 之前的内容）
        agent.messages = agent.messages[:cut_at]
        agent._saved_count = len(agent.messages)
        agent._tool_call_history.clear()

        # 重置上下文压缩计数器
        if agent.context_engine:
            agent.context_engine.on_session_reset()

        return (
            f"✅ 已回退 {n} 轮（移除 {removed} 条消息）\n"
            f"⚠ 仅恢复消息历史，已执行的工具操作不会被撤销"
        )

    return handler
