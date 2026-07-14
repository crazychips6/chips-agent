"""上下文感知路由 — 结合历史对话做路由决策

在规则预过滤之后、语义检索之前执行。
如果用户最近 3 轮对话都是同一意图，且当前消息短且相关，
复用上次意图，省掉 LLM 调用。

典型场景：
  用户: "帮我看看 report.pdf"
  Agent: [返回文档内容]
  用户: "总结一下"  ← 应该继续走 document 意图，而不是 simple_qa
"""

from __future__ import annotations

import logging
import re
from collections import deque
from typing import Any

logger = logging.getLogger("chips.agent.context_router")

# 短消息阈值（字符数）
_SHORT_MESSAGE_THRESHOLD = 20

# 指代词（表示"继续上文"的词）
_REFERENCE_WORDS = {
    "这个", "那个", "它", "他", "她", "上面", "刚才", "接着", "继续",
    "总结", "摘要", "分析", "解释", "翻译", "修改", "优化", "删除",
    "这个文件", "这个文档", "这个代码", "这段话",
}


class ContextRouter:
    """上下文感知路由器 — 基于历史对话意图做路由决策。"""

    def __init__(self, window_size: int = 5):
        self._window_size = window_size
        # 每个 session 的意图历史 {session_id: deque[intent]}
        self._intent_history: dict[str, deque[str]] = {}

    def update(self, session_id: str, intent: str):
        """更新会话的意图历史。"""
        if session_id not in self._intent_history:
            self._intent_history[session_id] = deque(maxlen=self._window_size)
        self._intent_history[session_id].append(intent)

    def get_recent_intents(self, session_id: str, top_n: int = 3) -> list[str]:
        """获取最近 N 个意图。"""
        history = self._intent_history.get(session_id)
        if not history:
            return []
        return list(history)[-top_n:]

    def match(self, text: str, session_id: str) -> str | None:
        """基于上下文匹配意图。

        返回复用的意图名或 None（需要继续走后续流程）。
        """
        if not session_id:
            return None

        recent_intents = self.get_recent_intents(session_id, top_n=3)
        if not recent_intents:
            return None

        # 策略 1：消息很短且包含指代词 → 复用最近意图
        if len(text) <= _SHORT_MESSAGE_THRESHOLD:
            has_reference = any(w in text for w in _REFERENCE_WORDS)
            if has_reference:
                last_intent = recent_intents[-1]
                # 排除 greeting/simple_qa（太泛，不适合复用）
                if last_intent not in ("greeting", "simple_qa", "other"):
                    logger.info("context_reuse intent=%s text=%s", last_intent, text[:30])
                    return last_intent

        # 策略 2：最近 3 轮都是同一意图 → 当前消息大概率还是同一意图
        if len(recent_intents) >= 3:
            if recent_intents[-1] == recent_intents[-2] == recent_intents[-3]:
                last_intent = recent_intents[-1]
                # 排除太泛的意图
                if last_intent not in ("greeting", "simple_qa", "other"):
                    logger.info("context_streak intent=%s count=3", last_intent)
                    return last_intent

        return None

    def clear(self, session_id: str):
        """清除会话的意图历史。"""
        self._intent_history.pop(session_id, None)


# 模块级单例
context_router = ContextRouter()
