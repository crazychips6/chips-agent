"""路由决策数据模型（精简版 — 只保留 block 相关）

ActionType 仅剩 block 和 direct，删除了 delegate/orchestrate/llm_router/handoff。
RouteDecision 同步简化。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Union

ActionType = Literal["direct", "block"]

# ── 条件树 ──


@dataclass
class LeafCondition:
    """叶子条件：对单个 fact 做比较。

    field: fact 名
    op: 操作符 eq/ne/in/contains/gte/lte/exists/match/not_in
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
        bool(set(fv) & set(v))
        if isinstance(fv, (list, tuple, set, frozenset))
        else fv in v
    ) if isinstance(v, (list, tuple, set, frozenset)) else False,
    "not_in": lambda fv, v: not (
        bool(set(fv) & set(v))
        if isinstance(fv, (list, tuple, set, frozenset))
        else fv in v
    ) if isinstance(v, (list, tuple, set, frozenset)) else True,
    "contains": lambda fv, v: bool(v in str(fv)) if fv is not None else False,
    "gte": lambda fv, v: (fv is not None) and (fv >= v),
    "lte": lambda fv, v: (fv is not None) and (fv <= v),
    "exists": lambda fv, _: fv is not None,
    "match": lambda fv, v: bool(re.search(str(v), str(fv))) if fv is not None else False,
}

# ── 规则 ──


@dataclass
class Rule:
    """一条安全拦截规则。"""

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
    """引擎对一次输入的判断结果。"""

    action: ActionType = "direct"
    reason: str = ""
    matched_rule: str = ""

    def is_block(self) -> bool:
        return self.action == "block"

    def __str__(self) -> str:
        if self.action == "block":
            return f"[block] {self.reason} (rule={self.matched_rule})"
        return f"[direct] {self.reason}"


# ── Action 解析 ──

_ACTION_RE = re.compile(r"^(\w+)\s*$")


def parse_action(action_str: str) -> ActionType:
    """解析动作字符串，返回 ActionType。

    仅支持 block / direct。
    """
    m = _ACTION_RE.match(action_str.strip())
    if not m:
        msg = f"无效的动作格式: '{action_str}'"
        raise ValueError(msg)
    action_name = m.group(1)
    valid_actions: set[str] = {"direct", "block"}
    if action_name not in valid_actions:
        msg = f"未知动作: '{action_name}'，可选: {', '.join(sorted(valid_actions))}"
        raise ValueError(msg)
    return action_name  # type: ignore[return-value]
