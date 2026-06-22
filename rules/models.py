"""路由决策数据模型

层次结构::

  Rule                    — 一条路由规则（来自 YAML）
  ├── name                — 规则名
  ├── priority            — 优先级（高优先先匹配）
  ├── condition           — 条件树（ConditionNode 或 LeafCondition）
  ├── then                — 动作字符串 "delegate(researcher)"
  └── reason              — 命中时输出的理由

  RouteDecision           — 引擎输出
  ├── action              — direct / block / handoff / delegate / llm_router
  ├── target              — 目标 Agent 名
  ├── reason              — 命中理由
  ├── matched_rule        — 命中的规则名
  └── confidence          — 决策置信度
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Union

ActionType = Literal["direct", "block", "handoff", "delegate", "orchestrate", "llm_router"]

# ── 条件树 ──


@dataclass
class LeafCondition:
    """叶子条件：对单个 fact 做比较。

    field: fact 名
    op: 操作符 eq/ne/in/contains/gte/gt/lte/lt/exists/match/not_in
    value: 比较值
    """

    field: str
    op: str
    value: Any = None

    def evaluate(self, facts: dict[str, Any]) -> bool:
        fact_val = facts.get(self.field)
        try:
            return _OPERATORS[self.op](fact_val, self.value)
        except (TypeError, ValueError, KeyError):
            return False


@dataclass
class GroupCondition:
    """组合条件：all（与）/ any（或）/ not（非）。"""

    op: Literal["all", "any", "not"]
    children: list[Union[LeafCondition, GroupCondition]] = field(default_factory=list)

    def evaluate(self, facts: dict[str, Any]) -> bool:
        if self.op == "all":
            return all(c.evaluate(facts) for c in self.children)
        elif self.op == "any":
            return any(c.evaluate(facts) for c in self.children)
        elif self.op == "not":
            return not any(c.evaluate(facts) for c in self.children) if self.children else True
        return True


# ── 条件操作符映射 ──

_OPERATORS: dict[str, Any] = {
    "eq": lambda fv, v: fv == v,
    "ne": lambda fv, v: fv != v,
    "in": lambda fv, v: (
        # 双方都是列表 → 交集非空（intent_keyword 含 "research"）
        bool(set(fv) & set(v))
        if isinstance(fv, (list, tuple, set, frozenset))
        # 标量 → 检查成员关系（标准 membership）
        else fv in v
    ) if isinstance(v, (list, tuple, set, frozenset)) else False,
    "not_in": lambda fv, v: not (
        bool(set(fv) & set(v))
        if isinstance(fv, (list, tuple, set, frozenset))
        else fv in v
    ) if isinstance(v, (list, tuple, set, frozenset)) else True,
    "contains": lambda fv, v: bool(v in str(fv)) if fv is not None else False,
    "gt": lambda fv, v: (fv is not None) and (fv > v),
    "gte": lambda fv, v: (fv is not None) and (fv >= v),
    "lt": lambda fv, v: (fv is not None) and (fv < v),
    "lte": lambda fv, v: (fv is not None) and (fv <= v),
    "exists": lambda fv, _: fv is not None,
    "match": lambda fv, v: bool(re.search(str(v), str(fv))) if fv is not None else False,
}

# ── 规则 ──


@dataclass
class Rule:
    """一条路由规则。"""

    name: str
    priority: int = 100
    condition: GroupCondition | LeafCondition | None = None
    then: str = "direct"
    reason: str = ""
    tests: list[dict] | None = None

    def matches(self, facts: dict[str, Any]) -> bool:
        if self.condition is None:
            return True
        return self.condition.evaluate(facts)


# ── 路由决策 ──


@dataclass
class RouteDecision:
    """引擎对一次输入的完整路由决策。"""

    action: ActionType = "direct"
    target: str = ""
    reason: str = ""
    matched_rule: str = ""
    confidence: float = 1.0
    plan: Any | None = None  # orchestrate 模式的执行计划

    def is_route(self) -> bool:
        """是否需要进行路由（非 direct 且非 block）。"""
        return self.action in ("handoff", "delegate", "orchestrate", "llm_router")

    def is_block(self) -> bool:
        return self.action == "block"

    def __str__(self) -> str:
        if self.action == "direct":
            return f"[direct] {self.reason}"
        elif self.action == "block":
            return f"[block] {self.reason} (rule={self.matched_rule})"
        elif self.action in ("handoff", "delegate"):
            return f"[{self.action} → {self.target}] {self.reason} (rule={self.matched_rule})"
        return f"[{self.action}] {self.reason} (rule={self.matched_rule})"


# ── Action 解析 ──

_ACTION_RE = re.compile(r"^(\w+)(?:\(([^)]*)\))?\s*$")


def parse_action(action_str: str) -> tuple[ActionType, str]:
    """解析动作字符串为 (action_type, target)。

    Examples::
        "direct"           → ("direct", "")
        "block"            → ("block", "")
        "delegate(researcher)" → ("delegate", "researcher")
        "handoff(support)" → ("handoff", "support")
        "llm_router"       → ("llm_router", "")
    """
    m = _ACTION_RE.match(action_str.strip())
    if not m:
        msg = f"无效的动作格式: '{action_str}'"
        raise ValueError(msg)
    action_name = m.group(1)
    target = m.group(2) or ""
    valid_actions: set[str] = {"direct", "block", "handoff", "delegate", "orchestrate", "llm_router"}
    if action_name not in valid_actions:
        msg = f"未知动作: '{action_name}'，可选: {', '.join(sorted(valid_actions))}"
        raise ValueError(msg)
    return action_name, target  # type: ignore[return-value]
