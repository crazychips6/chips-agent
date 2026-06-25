"""端侧小模型意图路由 — 使用 Ollama 上的小模型做意图检测+工具预选

在 RuleEngine（零成本关键字匹配）之后、ReAct 循环之前插入，
仅当本地模型可访问时激活，不可访问时无缝降级为原始路径。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("chips.rules.local_router")

_INTENT_PROMPT = """你是一个意图识别助手。分析用户最近一条消息，返回 JSON：

{
    "type": "simple_greeting" | "continuing" | "new_task",
    "reasoning": "简短判断理由（10字以内）",
    "tools": ["bash", "file"],
    "direct_reply": "你好！有什么可以帮你的？"
}

类型说明：
- simple_greeting：问候、打招呼、感谢、闲聊，不需要工具调用
- continuing："继续""接着""还有呢""然后呢"等延续性表达
- new_task：有具体目标的查询或操作，需要用工具完成

工具列表（仅 new_task 时填写可能需要的工具，最多2个最相关的）：
- bash：执行 shell 命令、查系统信息、运行脚本
- file：读取、编辑、搜索文件代码
- web：搜索网页、获取网页内容
- skills：执行已注册的技能

simple_greeting 时填写友好的 direct_reply。
不要输出其他内容，不要用 markdown 代码块，只返回 JSON。"""


class LocalRouter:
    """端侧小模型意图路由。"""

    OLLAMA_BASE = "http://localhost:11434"
    MODEL = "qwen2.5:0.5b"

    def __init__(self):
        self._available: bool | None = None

    # ── 可用性检测 ──

    def is_available(self) -> bool:
        """检测 Ollama 是否可访问。

        缓存策略：可用时缓存（避免每次消息都 ping），
        不可用时不缓存（启动时隧道没通、之后手动连上都可恢复）。
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
            logger.info("local_router_available")
        except Exception as exc:
            self._available = False
            logger.info("local_router_unavailable: %s", exc)
        return self._available

    def reset_availability(self):
        """清除可用性缓存，下次 is_available() 重新检测。"""
        self._available = None

    # ── 意图检测 ──

    def detect(self, user_message: str) -> dict:
        """检测用户意图。

        Returns:
            成功时返回 {type, reasoning, tools?, direct_reply?}
            失败时安全降级返回 {type: "continuing", reasoning: "..."}
        """
        payload = json.dumps({
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": _INTENT_PROMPT},
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
            # 清理可能的 markdown 代码块包裹
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                if len(lines) > 2:
                    content = "\n".join(lines[1:-1])
                else:
                    content = lines[-1]
            result = json.loads(content)
            if not isinstance(result, dict) or "type" not in result:
                raise ValueError("missing 'type' field in response")
            return result
        except Exception as exc:
            logger.warning("local_router_detect_failed: %s", exc)
            return {"type": "continuing", "reasoning": f"detect_error: {exc}"}

    # ── 工具查询（主 LLM 自救用） ──

    _QUERY_TOOLS_PROMPT = """分析用户任务，返回完成任务需要的工具列表。

返回 JSON：
{
    "tools": ["web", "bash"],
    "reasoning": "为什么需要这些工具"
}

可选工具：
- bash: shell 命令执行
- file: 文件读写搜索
- web: 网页搜索与内容抓取
- vision: 屏幕截图分析
- skills: 技能执行
- todo: 任务规划
- geo: 地理位置查询
- system: 系统信息
- process: 进程管理

只返回 JSON。"""

    def query_tools(self, task: str) -> dict:
        """主 LLM 调用：根据任务描述推荐工具列表。"""
        payload = json.dumps({
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": self._QUERY_TOOLS_PROMPT},
                {"role": "user", "content": task},
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 128},
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
            if not isinstance(result, dict) or "tools" not in result:
                raise ValueError("missing 'tools' field")
            return result
        except Exception as exc:
            logger.warning("local_router_query_tools_failed: %s", exc)
            return {"tools": [], "reasoning": f"query_error: {exc}"}

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
            logger.warning("local_router_analyze_trace_failed: %s", exc)
            return {"task": "unknown", "optimal": "", "waste": [], "tokens_saved_estimate": 0}

    # ── 快捷查询 ──

    @staticmethod
    def is_simple_greeting(intent: dict) -> bool:
        return intent.get("type") == "simple_greeting"

    @staticmethod
    def is_new_task(intent: dict) -> bool:
        return intent.get("type") == "new_task"

    @staticmethod
    def is_continuing(intent: dict) -> bool:
        return intent.get("type") == "continuing"
