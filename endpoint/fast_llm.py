"""FastLLM — 端侧小模型快速通道

在 GuardEngine（安全拦截）之后、ReAct 循环之前插入。

小模型只做三件事：问候、致谢、告别。
在这三个范围内自由回复，其他一律放行进 ReAct。

不可用时无缝降级（Ollama 不通 → 跳过）。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("chips.endpoint.fast_llm")

_CLASSIFY_PROMPT = """你是一个消息分类器。判断用户消息属于哪一类，只输出类别名。

类别：
- greeting: 问候、打招呼（你好/hi/hello/早上好）
- thanks: 感谢、致谢（谢谢/感谢/多谢）
- goodbye: 告别、再见（再见/拜拜/bye）
- other: 以上都不属于

示例：
  你好 → greeting
  谢谢 → thanks
  再见 → goodbye
  帮我搜索一下 → other
  今天天气怎么样 → other
  调查这个文件 → other

只输出类别名，不要其他内容。"""


class FastLLM:
    """端侧小模型快速通道。

    只处理 greeting / thanks / goodbye 三类，其他全放行。
    """

    OLLAMA_BASE = "http://localhost:11434"
    MODEL = "qwen2.5:1.5b-instruct-q4_K_S"

    def __init__(self):
        self._available: bool | None = None

    # ── 可用性检测 ──

    def is_available(self) -> bool:
        if self._available is True:
            return True
        try:
            req = urllib.request.Request(
                f"{self.OLLAMA_BASE}/api/tags",
                method="GET",
            )
            urllib.request.urlopen(req, timeout=3)
            self._available = True
            logger.info("fast_llm_available")
        except Exception as exc:
            self._available = False
            logger.info("fast_llm_unavailable: %s", exc)
        return self._available

    def reset_availability(self):
        self._available = None

    # ── 分类 + 回复 ──

    def answer(self, user_message: str) -> str | None:
        """尝试用端侧小模型回复。

        先分类，只有 greeting/thanks/goodbye 才回复，其他放行。

        Returns:
            回复文本（greeting/thanks/goodbye）
            None（other → 放行进 ReAct）
        """
        category = self._classify(user_message)
        if category == "other":
            return None
        if category:
            return self._reply(category, user_message)
        return None

    def _classify(self, user_message: str) -> str | None:
        """分类用户消息。返回 greeting / thanks / goodbye / other / None（失败时）。"""
        payload = json.dumps({
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": _CLASSIFY_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 16,
            },
        }).encode()

        req = urllib.request.Request(
            f"{self.OLLAMA_BASE}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=10)
            body = json.loads(resp.read())
            content = body.get("message", {}).get("content", "").strip().lower()
            if content in ("greeting", "thanks", "goodbye", "other"):
                logger.debug("fast_llm_classify result=%s msg=%s", content, user_message[:30])
                return content
            logger.debug("fast_llm_classify_unexpected result=%s", content)
            return "other"  # 分类异常时安全放行
        except Exception as exc:
            logger.warning("fast_llm_classify_failed: %s", exc)
            return None

    def _reply(self, category: str, user_message: str) -> str | None:
        """在指定分类范围内让小模型自由回复。"""
        category_prompt = {
            "greeting": "用户向你打招呼。用友好的语气回复，10 字以内。只输出回复内容。",
            "thanks": "用户向你道谢。用礼貌的语气回复，10 字以内。只输出回复内容。",
            "goodbye": "用户和你告别。用友好的语气回复，10 字以内。只输出回复内容。",
        }

        system = category_prompt.get(category, "")
        if not system:
            return None

        payload = json.dumps({
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 32,
            },
        }).encode()

        req = urllib.request.Request(
            f"{self.OLLAMA_BASE}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=10)
            body = json.loads(resp.read())
            reply = body.get("message", {}).get("content", "").strip()
            if reply:
                logger.info("fast_llm_reply category=%s reply=%s", category, reply[:60])
                return reply
            return None
        except Exception as exc:
            logger.warning("fast_llm_reply_failed category=%s error=%s", category, exc)
            return None

    # ── Trace 分析（学习用） ──

    _ANALYZE_TRACE_PROMPT = """你是一个 agent 执行分析器。分析完整的工具调用 trace，提取可复用的经验知识。

用户请求: {user_message}

执行步骤:
{trace_steps}

总步骤数: {total_steps}

返回 JSON：
{{
    "task": "任务类型（如：天气查询、代码搜索）",
    "optimal": "最优执行路径描述（50字以内，下次可直接用）",
    "waste": ["浪费的操作1", "浪费的操作2"],
    "tokens_saved_estimate": 估计省了多少 token（整数）
}}

只返回 JSON。"""

    def analyze_trace(self, trace: dict) -> dict:
        """分析 trace，提取最优路径和失败模式。"""
        steps = []
        for i, tc in enumerate(trace.get("tool_calls", []), 1):
            name = tc.get("name", "?")
            args = tc.get("args", "{}")
            result_preview = (tc.get("result", "") or "")[:120]
            steps.append(f"  Step {i}: {name}({args})")
            if result_preview:
                steps.append(f"    → {result_preview}")

        trace_steps = "\n".join(steps)
        prompt = self._ANALYZE_TRACE_PROMPT.format(
            user_message=(trace.get("user_message", "") or "")[:200],
            trace_steps=trace_steps,
            total_steps=trace.get("total_steps", 0),
        )

        payload = json.dumps({
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "分析以上 trace。"},
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 256},
        }).encode()

        req = urllib.request.Request(
            f"{self.OLLAMA_BASE}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            body = json.loads(resp.read())
            content = body.get("message", {}).get("content", "")
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
            result = json.loads(content)
            if not isinstance(result, dict) or "task" not in result:
                raise ValueError("missing 'task' field")
            return result
        except Exception as exc:
            logger.warning("fast_llm_analyze_trace_failed: %s", exc)
            return {"task": "unknown", "optimal": "", "waste": [], "tokens_saved_estimate": 0}
