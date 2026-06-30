"""FastLLM — 端侧小模型分类器

不再做回复拦截，只做意图分类 + 工具预测。
路由层根据分类结果决定走小模型还是大模型通道。

不可用时无缝降级（Ollama 不通 → 跳过）。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("chips.endpoint.fast_llm")

_CLASSIFY_PROMPT = """你是一个消息分类器。分析用户消息，输出候选意图。

返回格式：
{"candidates": [{"intent": "意图名", "predicted_tools": ["工具"], "score": 0-100}]}

intent 可选：
- greeting: 问候打招呼
- simple_qa: 简单常识
- web_search: 需搜索网页
- simple_coding: 简单编码
- complex: 复杂分析
- delegate: 需子 Agent
- other: 以上都不属于

predicted_tools 可选：web, bash, file, orchestrate, sub_agent

示例：
  你好 → {"candidates": [{"intent": "greeting", "predicted_tools": [], "score": 95}, {"intent": "other", "predicted_tools": [], "score": 5}]}
  今天天气 → {"candidates": [{"intent": "web_search", "predicted_tools": ["web"], "score": 90}, {"intent": "simple_qa", "predicted_tools": [], "score": 10}]}

只输出 JSON。"""


class FastLLM:
    """端侧小模型分类器。

    只做分类和工具预测，不回复内容。
    """

    OLLAMA_BASE = "http://localhost:11434"
    MODEL = "qwen2.5:1.5b-instruct-q4_K_S"

    def __init__(self):
        self._available: bool | None = None

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

    def classify(self, user_message: str) -> dict:
        """分类用户消息，返回 {intent, predicted_tools, candidates, confidence}。

        intent 为候选列表中分数最高的合法标签。
        失败时安全降级返回 other。
        """
        payload = json.dumps({
            "model": self.MODEL,
            "format": "json",
            "messages": [
                {"role": "system", "content": _CLASSIFY_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 128,
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
            content = body.get("message", {}).get("content", "")
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
            parsed = json.loads(content)
            candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
            validated = []
            from agent.intent_config import INTENT_ROUTES
            valid_labels = set(INTENT_ROUTES.keys())
            for c in candidates:
                if isinstance(c, dict) and c.get("intent") in valid_labels:
                    validated.append(c)
            validated.sort(key=lambda x: x.get("score", 0), reverse=True)
            best = validated[0] if validated else {"intent": "other", "predicted_tools": [], "score": 0}
            logger.info("fast_llm_classify intent=%s tools=%s score=%s candidates=%d",
                        best["intent"], best.get("predicted_tools", []),
                        best.get("score"), len(validated))
            return {
                "intent": best["intent"],
                "predicted_tools": best.get("predicted_tools", []),
                "candidates": validated,
                "confidence": "high" if validated else "low",
            }
        except Exception as exc:
            logger.warning("fast_llm_classify_failed: %s", exc)
            return {"intent": "other", "predicted_tools": [], "candidates": [], "confidence": "low"}

    # ── Trace 分析（保留，不变） ──

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

        try:
            resp = urllib.request.urlopen(payload, timeout=15)
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
