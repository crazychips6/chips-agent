"""规则预过滤层 — 高频确定性意图直接走规则，不浪费 LLM 调用

在 FastLLM.classify() 之前执行，规则命中直接返回意图。

规则类型：
  - exact: 精确匹配（消息去除空白后完全相等）
  - prefix: 前缀匹配（消息以指定字符串开头）
  - contains: 包含匹配（消息包含指定字符串）
  - regex: 正则匹配（消息匹配正则表达式）
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent.intent_loader import intent_registry

logger = logging.getLogger("chips.agent.rule_matcher")


class Rule:
    """单条匹配规则。"""

    def __init__(self, data: dict[str, Any], intent_name: str):
        self.intent_name = intent_name
        self.type: str = data.get("type", "")
        self.values: list[str] = data.get("values", [])
        self.pattern: str = data.get("pattern", "")
        self._compiled_regex: re.Pattern | None = None

        if self.type == "regex" and self.pattern:
            try:
                self._compiled_regex = re.compile(self.pattern, re.IGNORECASE)
            except re.error as e:
                logger.warning("rule_regex_compile_failed intent=%s pattern=%s error=%s",
                               intent_name, self.pattern, e)

    def match(self, text: str) -> bool:
        """检查文本是否匹配此规则。"""
        if self.type == "exact":
            return text.strip() in self.values
        elif self.type == "prefix":
            return any(text.startswith(v) for v in self.values)
        elif self.type == "contains":
            return any(v in text for v in self.values)
        elif self.type == "regex" and self._compiled_regex:
            return bool(self._compiled_regex.search(text))
        return False


class RuleMatcher:
    """规则预过滤器 — 从意图配置加载规则，按优先级匹配。"""

    def __init__(self):
        self._rules: list[Rule] = []
        self._load_rules()

    def _load_rules(self):
        """从所有意图配置加载规则。"""
        self._rules.clear()
        intents = intent_registry.get_all()

        # 按优先级排序：exact > prefix > contains > regex
        priority = {"exact": 0, "prefix": 1, "contains": 2, "regex": 3}
        all_rules: list[tuple[int, Rule]] = []

        for name, intent in intents.items():
            if not hasattr(intent, 'rules'):
                continue
            for rule_data in intent.rules:
                rule = Rule(rule_data, name)
                if rule.type and (rule.values or rule.pattern):
                    p = priority.get(rule.type, 99)
                    all_rules.append((p, rule))

        # 按优先级排序（高优先级先匹配）
        all_rules.sort(key=lambda x: x[0])
        self._rules = [r for _, r in all_rules]

        logger.info("rules_loaded count=%d", len(self._rules))

    def reload(self):
        """热重载规则。"""
        self._load_rules()

    def match(self, text: str) -> ClassifyResult | None:
        """匹配文本，返回 ClassifyResult 或 None。

        按优先级顺序匹配，返回第一个命中的意图。
        """
        from agent.classify_result import ClassifyResult, PRIORITY_RULE
        from agent.intent_loader import intent_registry

        for rule in self._rules:
            if rule.match(text):
                logger.info("rule_match intent=%s type=%s text=%s",
                            rule.intent_name, rule.type, text[:30])
                intent_def = intent_registry.get(rule.intent_name)
                predicted_tools = []
                if intent_def and isinstance(intent_def.tools, list):
                    predicted_tools = intent_def.tools
                return ClassifyResult(
                    intent=rule.intent_name,
                    confidence=1.0,  # 规则匹配置信度为 1
                    priority=PRIORITY_RULE,
                    source="rule",
                    predicted_tools=predicted_tools,
                )
        return None


# 模块级单例
rule_matcher = RuleMatcher()
