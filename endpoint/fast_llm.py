"""FastLLM — 端侧小模型多功能处理器

核心能力：
  1. 意图分类（四层架构 + 仲裁）
  2. Trace 分析（提取经验知识）
  3. 工具参数预生成（减少大模型 token 消耗）
  4. 工具结果后处理（格式化、关键信息提取）
  5. 对话质量自评估（检测幻觉、逻辑矛盾）

所有功能通过 Ollama qwen2.5:1.5b 本地运行，零 API 成本。
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

    四层分类 + 仲裁机制。
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

    def classify(self, user_message: str, session_id: str = "") -> dict:
        """分类用户消息，返回 {intent, predicted_tools, candidates, confidence, source}。

        四层分类 + 仲裁：
        1. 规则匹配（~0ms）→ 命中直接返回
        2. 上下文匹配（~0ms）→ 复用历史意图
        3. 语义检索（~10ms）→ TF-IDF/Embedding
        4. LLM 分类（~500ms）→ 仅前三层未命中时调用
        仲裁：高置信度（>=0.95）直接采信，否则按优先级排序
        """
        from agent.classify_result import ClassifyResult, arbitrate

        results: list[ClassifyResult] = []

        # ── 阶段一：规则预过滤（~0ms）──
        from agent.rule_matcher import rule_matcher
        rule_result = rule_matcher.match(user_message)
        if rule_result:
            # 规则匹配置信度为 1.0，直接返回
            logger.info("classify_source=rule intent=%s text=%s",
                        rule_result.intent, user_message[:30])
            self._update_context(session_id, rule_result.intent)
            return rule_result.to_dict()

        # ── 阶段二：上下文匹配（~0ms）──
        from agent.context_router import context_router
        context_result = context_router.match(user_message, session_id)
        if context_result:
            results.append(context_result)

        # ── 阶段三：语义路由（~10ms）──
        # 同时完成意图分类和工具推荐
        from agent.semantic_router import semantic_router
        try:
            semantic_route = semantic_router.route(user_message)
            if semantic_route and semantic_route["confidence"] >= 0.5:
                results.append(ClassifyResult(
                    intent=semantic_route["intent"],
                    confidence=semantic_route["confidence"],
                    priority=PRIORITY_SEMANTIC,
                    source="semantic",
                    predicted_tools=semantic_route["tools"],
                    candidates=[
                        {"intent": name, "score": int(score * 100)}
                        for name, score in semantic_route.get("top_intents", [])
                    ],
                ))
        except Exception as e:
            logger.debug("semantic_router_failed: %s", e)
            # fallback 到旧的语义匹配器
            from agent.semantic_matcher import semantic_matcher
            semantic_result = semantic_matcher.match_one(user_message, threshold=0.5)
            if semantic_result:
                results.append(semantic_result)

        # ── 阶段四：LLM 分类（~500ms）──
        # 只有当前三层都没有高置信度结果时才调用 LLM
        should_call_llm = True
        for r in results:
            if r.confidence >= 0.7:
                should_call_llm = False
                break

        if should_call_llm:
            llm_result = self._llm_classify(user_message)
            if llm_result:
                results.append(llm_result)

        # ── 仲裁 ──
        final = arbitrate(results)
        if final:
            logger.info("classify_source=%s intent=%s confidence=%.2f text=%s",
                        final.source, final.intent, final.confidence, user_message[:30])
            self._update_context(session_id, final.intent)
            return final.to_dict()

        # 兜底
        return {"intent": "other", "predicted_tools": [], "candidates": [],
                "confidence": 0, "source": "fallback"}

    def _update_context(self, session_id: str, intent: str):
        """更新上下文历史。"""
        if session_id:
            from agent.context_router import context_router
            context_router.update(session_id, intent)

    def _llm_classify(self, user_message: str) -> ClassifyResult | None:
        """调用 LLM 分类。"""
        from agent.classify_result import ClassifyResult, PRIORITY_LLM
        from agent.intent_loader import intent_registry

        if not self.is_available():
            return None

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
            valid_labels = set(intent_registry.get_all().keys())
            for c in candidates:
                if isinstance(c, dict) and c.get("intent") in valid_labels:
                    validated.append(c)
            validated.sort(key=lambda x: x.get("score", 0), reverse=True)
            best = validated[0] if validated else None
            if not best:
                return None

            intent_def = intent_registry.get(best["intent"])
            predicted_tools = []
            if intent_def and isinstance(intent_def.tools, list):
                predicted_tools = intent_def.tools

            # LLM 分类的置信度基于 score（0-100 映射到 0-1）
            confidence = best.get("score", 50) / 100.0

            return ClassifyResult(
                intent=best["intent"],
                confidence=confidence,
                priority=PRIORITY_LLM,
                source="llm",
                predicted_tools=predicted_tools or best.get("predicted_tools", []),
                candidates=validated,
            )
        except Exception as exc:
            logger.warning("llm_classify_failed: %s", exc)
            return None

    # ── Trace 分析（增强版） ──

    _ANALYZE_TRACE_PROMPT = """你是一个 agent 执行分析器。分析完整的工具调用 trace，提取可复用的经验知识。

用户请求: {user_message}
执行意图: {intent}

执行步骤:
{trace_steps}

总步骤数: {total_steps}
最终结果: {final_reply}

请分析并返回 JSON：
{{
    "task": "任务类型（如：天气查询、代码搜索、文档解析）",
    "triggers": ["触发词1", "触发词2"],
    "pattern": "什么时候该用这条经验（30字以内）",
    "steps": [
        {{"tool": "工具名", "args_pattern": "参数模式", "result_pattern": "结果模式", "is_optional": false}}
    ],
    "summary": "一句话总结最优路径",
    "anti_patterns": ["不该做的事1", "不该做的事2"],
    "pitfalls": ["容易踩的坑1"],
    "tokens_saved_estimate": 150,
    "quality_score": 0.8
}}

quality_score 评分标准：
- 1.0: 完美执行，无冗余步骤
- 0.7-0.9: 基本最优，有小瑕疵
- 0.4-0.6: 有效但有浪费
- <0.4: 不值得记录

只返回 JSON。"""

    def analyze_trace(self, trace: dict, intent: str = "") -> dict:
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
            intent=intent or "unknown",
            trace_steps=trace_steps,
            total_steps=trace.get("total_steps", 0),
            final_reply=(trace.get("final_reply", "") or "")[:200],
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

    # ── 工具参数预生成 ──

    _TOOL_ARGS_PROMPT = """你是一个工具参数生成器。根据用户意图和工具定义，生成工具调用参数。

用户意图: {intent}
工具名称: {tool_name}
工具描述: {tool_description}
工具参数 schema: {tool_schema}

用户消息: {user_message}

请生成最可能的工具调用参数，返回 JSON 格式：
{{"param1": "value1", "param2": "value2"}}

只返回 JSON，不要其他内容。"""

    def generate_tool_args(self, tool_name: str, tool_description: str,
                           tool_schema: dict, user_message: str, intent: str = "") -> dict | None:
        """预生成工具调用参数。

        用于在大模型调用前预填充参数，减少 token 消耗。
        返回参数字典或 None（生成失败时）。
        """
        if not self.is_available():
            return None

        prompt = self._TOOL_ARGS_PROMPT.format(
            intent=intent or "unknown",
            tool_name=tool_name,
            tool_description=tool_description[:200],
            tool_schema=json.dumps(tool_schema, ensure_ascii=False)[:500],
            user_message=user_message[:200],
        )

        payload = json.dumps({
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "生成参数。"},
            ],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 128},
        }).encode()

        try:
            req = urllib.request.Request(
                f"{self.OLLAMA_BASE}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=5)
            body = json.loads(resp.read())
            content = body.get("message", {}).get("content", "").strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
            result = json.loads(content)
            if isinstance(result, dict):
                logger.info("tool_args_generated tool=%s args=%s", tool_name, list(result.keys()))
                return result
        except Exception as exc:
            logger.debug("tool_args_generation_failed tool=%s: %s", tool_name, exc)
        return None

    # ── 工具结果后处理 ──

    _RESULT_POSTPROCESS_PROMPT = """你是一个工具结果处理器。从工具返回的大量内容中提取关键信息。

工具名称: {tool_name}
用户问题: {user_question}
工具返回内容:
{tool_result}

请提取关键信息，返回 JSON 格式：
{{
    "summary": "一句话总结关键信息",
    "key_points": ["要点1", "要点2"],
    "is_relevant": true/false
}}

只返回 JSON。"""

    def postprocess_tool_result(self, tool_name: str, tool_result: str,
                                user_question: str = "") -> dict | None:
        """后处理工具结果，提取关键信息。

        用于压缩工具返回的大段内容，减少后续 LLM 的 token 消耗。
        返回 {summary, key_points, is_relevant} 或 None。
        """
        if not self.is_available():
            return None

        # 结果太短不需要处理
        if len(tool_result) < 500:
            return None

        prompt = self._RESULT_POSTPROCESS_PROMPT.format(
            tool_name=tool_name,
            user_question=user_question[:200] or "无",
            tool_result=tool_result[:2000],
        )

        payload = json.dumps({
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "处理结果。"},
            ],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 256},
        }).encode()

        try:
            req = urllib.request.Request(
                f"{self.OLLAMA_BASE}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=10)
            body = json.loads(resp.read())
            content = body.get("message", {}).get("content", "").strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
            result = json.loads(content)
            if isinstance(result, dict):
                logger.info("tool_result_postprocessed tool=%s relevant=%s",
                            tool_name, result.get("is_relevant"))
                return result
        except Exception as exc:
            logger.debug("tool_result_postprocess_failed tool=%s: %s", tool_name, exc)
        return None

    # ── 对话质量自评估 ──

    _QUALITY_EVAL_PROMPT = """你是一个对话质量评估器。评估 AI 回复的质量。

用户问题: {user_question}
AI 回复: {ai_reply}

请评估并返回 JSON：
{{
    "quality_score": 0.0-1.0,
    "issues": ["问题1", "问题2"],
    "suggestions": ["建议1", "建议2"],
    "has_hallucination": true/false,
    "is_helpful": true/false
}}

评分标准：
- 1.0: 完美回答，准确且有帮助
- 0.7-0.9: 良好，有小瑕疵
- 0.4-0.6: 一般，有明显问题
- <0.4: 差，可能有幻觉或完全无帮助

只返回 JSON。"""

    def evaluate_reply_quality(self, user_question: str, ai_reply: str) -> dict | None:
        """评估 AI 回复质量。

        用于检测幻觉、逻辑矛盾、无帮助的回复。
        返回 {quality_score, issues, suggestions, has_hallucination, is_helpful} 或 None。
        """
        if not self.is_available():
            return None

        # 回复太短不需要评估
        if len(ai_reply) < 50:
            return None

        prompt = self._QUALITY_EVAL_PROMPT.format(
            user_question=user_question[:300],
            ai_reply=ai_reply[:1000],
        )

        payload = json.dumps({
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "评估质量。"},
            ],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 256},
        }).encode()

        try:
            req = urllib.request.Request(
                f"{self.OLLAMA_BASE}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=10)
            body = json.loads(resp.read())
            content = body.get("message", {}).get("content", "").strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
            result = json.loads(content)
            if isinstance(result, dict):
                logger.info("quality_eval score=%.2f hallucination=%s",
                            result.get("quality_score", 0),
                            result.get("has_hallucination", False))
                return result
        except Exception as exc:
            logger.debug("quality_eval_failed: %s", exc)
        return None
