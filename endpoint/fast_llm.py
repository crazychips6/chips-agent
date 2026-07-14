"""FastLLM — 端侧小模型分类器

不再做回复拦截，只做意图分类 + 工具预测。
路由层根据分类结果决定走小模型还是大模型通道。

不可用时无缝降级（Ollama 不通 → 跳过）。

分类 prompt 从 agent/intents/*.yaml 自动构建：
  - 新增意图只需加 YAML 文件，不改此处代码
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("chips.endpoint.fast_llm")


def _build_classify_prompt() -> str:
    """从意图配置自动构建分类 prompt。"""
    from agent.intent_loader import intent_registry

    intents = intent_registry.get_all()
    all_keywords = intent_registry.get_all_keywords()

    # 构建 intent 列表
    intent_lines = []
    for name, intent in intents.items():
        if name == "other":
            continue
        desc_parts = []
        if intent.keywords:
            desc_parts.append(f"关键词：{', '.join(intent.keywords[:5])}")
        desc = "（" + "；".join(desc_parts) + "）" if desc_parts else ""
        intent_lines.append(f"- {name}: {desc}")

    intent_list = "\n".join(intent_lines)

    # 构建规则
    rules = []
    for name, intent in intents.items():
        if intent.time_sensitive:
            kw = ", ".join(intent.keywords[:5])
            rules.append(f'消息含"{kw}"等时间敏感词时，优先选 {name}。')
        if intent.keywords and name not in ("greeting", "simple_qa", "other"):
            kw = ", ".join(intent.keywords[:5])
            rules.append(f'消息含文件路径或"{kw}"等词时，优先选 {name}。')

    rules_text = "\n".join(rules) if rules else "无特殊规则。"

    # 构建 predicted_tools 列表
    all_tools = set()
    for intent in intents.values():
        if isinstance(intent.tools, list):
            all_tools.update(intent.tools)
    tools_list = ", ".join(sorted(all_tools - {""}))

    prompt = f"""你是一个消息分类器。分析用户消息，输出候选意图。

返回格式：
{{"candidates": [{{"intent": "意图名", "predicted_tools": ["工具"], "score": 0-100}}]}}

intent 可选：
{intent_list}
- other: 以上都不属于

关键规则：
{rules_text}

predicted_tools 可选：{tools_list}

示例：
  你好 → {{"candidates": [{{"intent": "greeting", "predicted_tools": [], "score": 95}}, {{"intent": "other", "predicted_tools": [], "score": 5}}]}}
  今天天气 → {{"candidates": [{{"intent": "web_search", "predicted_tools": ["web"], "score": 90}}, {{"intent": "simple_qa", "predicted_tools": [], "score": 10}}]}}

只输出 JSON。"""

    return prompt


# 模块级缓存（启动时构建一次）
_CLASSIFY_PROMPT: str | None = None


def _get_classify_prompt() -> str:
    """获取分类 prompt（懒加载 + 缓存）。"""
    global _CLASSIFY_PROMPT
    if _CLASSIFY_PROMPT is None:
        _CLASSIFY_PROMPT = _build_classify_prompt()
    return _CLASSIFY_PROMPT


def reload_classify_prompt():
    """强制重新构建分类 prompt（用于热重载）。"""
    global _CLASSIFY_PROMPT
    _CLASSIFY_PROMPT = _build_classify_prompt()


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

        三层分类架构：
        1. 规则匹配（~0ms）→ 命中直接返回
        2. 语义检索（~10ms）→ TF-IDF 候选 Top-K
        3. LLM 分类（~500ms）→ 仅前两层未命中时调用
        """
        from agent.intent_loader import intent_registry

        # ── 阶段一：规则预过滤（~0ms）──
        from agent.rule_matcher import rule_matcher
        rule_intent = rule_matcher.match(user_message)
        if rule_intent:
            intent_def = intent_registry.get(rule_intent)
            predicted_tools = []
            if intent_def and isinstance(intent_def.tools, list):
                predicted_tools = intent_def.tools
            logger.info("classify_source=rule intent=%s text=%s", rule_intent, user_message[:30])
            return {
                "intent": rule_intent,
                "predicted_tools": predicted_tools,
                "candidates": [{"intent": rule_intent, "predicted_tools": predicted_tools, "score": 100}],
                "confidence": "high",
                "source": "rule",
            }

        # ── 阶段二：语义检索（~10ms）──
        from agent.semantic_matcher import semantic_matcher
        semantic_candidates = semantic_matcher.match(user_message, top_k=3, threshold=0.15)
        if semantic_candidates:
            best_intent, best_score = semantic_candidates[0]
            # 高置信度（>0.5）直接返回，不走 LLM
            if best_score >= 0.5:
                intent_def = intent_registry.get(best_intent)
                predicted_tools = []
                if intent_def and isinstance(intent_def.tools, list):
                    predicted_tools = intent_def.tools
                logger.info("classify_source=semantic intent=%s score=%.2f text=%s",
                            best_intent, best_score, user_message[:30])
                return {
                    "intent": best_intent,
                    "predicted_tools": predicted_tools,
                    "candidates": [{"intent": i, "predicted_tools": [], "score": int(s * 100)}
                                   for i, s in semantic_candidates],
                    "confidence": "high",
                    "source": "semantic",
                }

        # ── 阶段三：LLM 分类（~500ms）──
        classify_prompt = _get_classify_prompt()

        payload = json.dumps({
            "model": self.MODEL,
            "format": "json",
            "messages": [
                {"role": "system", "content": classify_prompt},
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
            from agent.intent_loader import intent_registry
            valid_labels = set(intent_registry.get_all().keys())
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
