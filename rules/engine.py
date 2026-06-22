"""RuleEngine — 路由规则引擎

职责：
  1. 加载规则（YAML → Rule 对象列表）
  2. 从用户输入提取事实
  3. 按优先级匹配规则
  4. 返回 RouteDecision

三层调用链::

    RuleEngine.evaluate(text)
      ├─ _extract_low_cost_facts()     → 始终执行（~0ms）
      ├─ _try_match_low_cost()         → 用低代价 fact 匹配规则
      │   ├─ 命中 → 返回决策
      │   └─ 未命中 → 继续
      ├─ _extract_medium_cost_facts()  → 按需执行
      └─ _try_match_all()              → 用全部 fact 匹配规则
          ├─ 命中 → 返回决策
          └─ 未命中 → 返回默认 direct

集成方式::

    from rules.engine import RuleEngine
    engine = RuleEngine()
    decision = engine.evaluate("帮我搜索一下 AI 框架")
    if decision.is_route():
        # 执行路由
    elif decision.is_block():
        # 拦截
    else:
        # 正常 ReAct 循环
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from rules.facts import FactRegistry, extract_facts
from rules.loader import load_rules
from rules.models import (
    GroupCondition,
    LeafCondition,
    RouteDecision,
    parse_action,
)

logger = logging.getLogger("chips.rules.engine")

# 事实提取的代价等级控制
_COST_LOW = "low"
_COST_MEDIUM = "medium"


class RuleEngine:
    """路由规则引擎。

    Args:
        user_rules_path: 用户自定义规则路径
        local_rules_path: 项目本地规则路径
        include_defaults: 是否加载默认规则
        tool_names: 已知工具名列表（用于 mentions_tool fact）
    """

    def __init__(
        self,
        user_rules_path: str | Path | None = None,
        local_rules_path: str | Path | None = None,
        include_defaults: bool = True,
        tool_names: set[str] | None = None,
    ):
        self._rules = load_rules(
            user_path=Path(user_rules_path) if user_rules_path else None,
            local_path=Path(local_rules_path) if local_rules_path else None,
            include_defaults=include_defaults,
        )
        self._tool_names: set[str] = tool_names or set()
        self._stats: dict[str, Any] = {
            "evaluations": 0,
            "low_cost_hits": 0,
            "medium_cost_hits": 0,
            "fallback_direct": 0,
            "total_latency_ms": 0,
        }

    @property
    def stats(self) -> dict[str, Any]:
        return dict(self._stats)

    @property
    def rules(self) -> list:
        return list(self._rules)

    def reload(self) -> None:
        """重新加载规则（在运行时修改 routing.yaml 后调用）。"""
        self._rules = load_rules(
            user_path=None,
            local_path=None,
            include_defaults=True,
        )

    # ── 主入口 ──

    def evaluate(
        self,
        text: str,
        *,
        extra_facts: dict[str, Any] | None = None,
    ) -> RouteDecision:
        """对用户输入执行路由判断。

        Args:
            text: 用户输入文本
            extra_facts: 外部注入的额外事实（如 tool_names、session 状态等）

        Returns:
            RouteDecision
        """
        self._stats["evaluations"] += 1

        # Stage 1: 提取低代价事实
        low_facts = extract_facts(text, cost=_COST_LOW, tool_names=self._tool_names)
        if extra_facts:
            low_facts.update(extra_facts)

        # Stage 2: 用低代价事实匹配规则
        decision = self._match_first(self._rules, low_facts)
        if decision is not None:
            self._stats["low_cost_hits"] += 1
            return decision

        # Stage 3: 提取中代价事实（仅当低代价匹配失败时）
        medium_facts = extract_facts(text, cost=_COST_MEDIUM, tool_names=self._tool_names)
        all_facts = {**low_facts, **medium_facts}
        if extra_facts:
            all_facts.update(extra_facts)

        decision = self._match_first(self._rules, all_facts)
        if decision is not None:
            self._stats["medium_cost_hits"] += 1
            return decision

        # Stage 4: 无规则命中 → 默认 direct
        self._stats["fallback_direct"] += 1
        return RouteDecision(
            action="direct",
            reason="无匹配规则，走默认行为",
        )

    # ── 规则匹配 ──

    def _match_first(
        self,
        rules: list,
        facts: dict[str, Any],
    ) -> RouteDecision | None:
        """按优先级降序遍历规则，返回第一个命中的决策。"""
        for rule in rules:
            if rule.matches(facts):
                action, target = parse_action(rule.then)
                return RouteDecision(
                    action=action,
                    target=target,
                    reason=rule.reason or f"命中规则: {rule.name}",
                    matched_rule=rule.name,
                )
        return None

    # ── 可观测性 ──

    def summary(self) -> str:
        """输出引擎统计摘要。"""
        total = self._stats["evaluations"]
        if total == 0:
            return "RuleEngine: 尚无评估记录"

        low_pct = self._stats["low_cost_hits"] / total * 100
        med_pct = self._stats["medium_cost_hits"] / total * 100
        fallback_pct = self._stats["fallback_direct"] / total * 100

        return (
            f"RuleEngine 统计 (共 {total} 次评估):\n"
            f"  低代价命中: {self._stats['low_cost_hits']} ({low_pct:.1f}%)\n"
            f"  中代价命中: {self._stats['medium_cost_hits']} ({med_pct:.1f}%)\n"
            f"  默认直通:   {self._stats['fallback_direct']} ({fallback_pct:.1f}%)"
        )

    def test_rules(self) -> list[dict]:
        """运行所有规则的测试用例（仅测试有 tests 字段的规则）。"""
        results: list[dict] = []
        for rule in self._rules:
            if not rule.tests:
                continue
            for t in rule.tests:
                input_text = t.get("input", "")
                expected_action = t.get("expected", "direct")
                decision = self.evaluate(input_text)
                passed = decision.action == expected_action
                results.append({
                    "rule": rule.name,
                    "input": input_text,
                    "expected": expected_action,
                    "got": decision.action,
                    "passed": passed,
                })
        return results
