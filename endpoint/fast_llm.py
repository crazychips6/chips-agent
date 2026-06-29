"""FastLLM — 端侧小模型快速通道

在 GuardEngine（安全拦截）之后、ReAct 循环之前插入。
只问一个问题："端侧小模型能直接回复吗？"
  能 → 直接回复，省一次 API 调用
  不能 → 放行进 ReAct

不可用时无缝降级（Ollama 不通 → 跳过）。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("chips.endpoint.fast_llm")

_FAST_PROMPT = """判断能否直接回复。输出 {"confident": true/false, "reply": "..."}

可自信回复的场景（confident=true）：
- 问候、感谢、道别
- 纯百科常识（首都、数学、定义等不需要搜索的知识）
- 闲聊

必须放行的场景（confident=false）：
- 需要搜索、查询、查找信息
- 需要执行命令或代码
- 需要操作文件
- 复杂或多步骤任务
- 需要实时信息（天气、新闻、股价等）
- 不确定答案

示例：
你好 → {"confident": true, "reply": "你好！"}
谢谢 → {"confident": true, "reply": "不客气！"}
中国的首都是哪里 → {"confident": true, "reply": "北京"}
1+1等于几 → {"confident": true, "reply": "2"}
搜索最新的AI框架 → {"confident": false, "reply": ""}
帮我查一下Rust和Go的对比 → {"confident": false, "reply": ""}
帮我写个Python脚本 → {"confident": false, "reply": ""}
今天天气怎么样 → {"confident": false, "reply": ""}
读readme.md → {"confident": false, "reply": ""}
分析这份报告 → {"confident": false, "reply": ""}

只输出JSON。"""

# 触发放行的关键词 — 包含这些词的请求不经过小模型，直接放行进 ReAct
_TRIGGER_KEYWORDS = [
    "搜索", "查一", "查查", "查找", "查询", "搜一",
    "写代码", "写个", "写一个", "编写",
    "读文件", "读一下", "读取", "打开文件",
    "执行", "运行", "安装", "下载",
    "天气", "新闻", "股票", "股价",
    "最新", "对比", "分析", "总结",
    "翻译", "代码", "脚本", "命令",
]


class FastLLM:
    """端侧小模型快速通道。

    使用 Ollama 上的小模型做低成本的直接回复。
    """

    OLLAMA_BASE = "http://localhost:11434"
    MODEL = "qwen2.5:1.5b-instruct-q4_K_S"

    def __init__(self):
        self._available: bool | None = None

    # ── 可用性检测 ──

    def is_available(self) -> bool:
        """检测 Ollama 是否可访问。

        缓存策略：可用时缓存，不可用时不缓存（隧道恢复后可自动重连）。
        """
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
        """清除可用性缓存，下次 is_available() 重新检测。"""
        self._available = None

    # ── 快速回复 ──

    def answer(self, user_message: str) -> str | None:
        """尝试用端侧小模型直接回复。

        Args:
            user_message: 用户消息

        Returns:
            自信能答 → 回复文本
            没把握    → None（放行进 ReAct）
        """
        # 前置关键词检：含"搜索""写代码""读文件"等关键字的请求直接放行
        msg_lower = user_message.lower()
        for kw in _TRIGGER_KEYWORDS:
            if kw in msg_lower:
                logger.debug("fast_llm_trigger_skip keyword=%s msg=%s", kw, user_message[:40])
                return None

        payload = json.dumps({
            "model": self.MODEL,
            "format": "json",
            "messages": [
                {"role": "system", "content": _FAST_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {
                "temperature": 0.1,
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
            result = json.loads(content)

            if result.get("confident") and result.get("reply"):
                reply = result["reply"].strip()
                logger.info("fast_llm_answered reply=%s", reply[:60])
                return reply

            logger.info("fast_llm_not_confident")
            return None

        except Exception as exc:
            logger.warning("fast_llm_answer_failed: %s", exc)
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
