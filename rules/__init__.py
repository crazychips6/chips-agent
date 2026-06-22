"""Agent 路由规则引擎

三层递进判断：规则引擎 → LLM Router → Fallback。
当前仅实现规则引擎层，后续扩展 LLM Router。

用法::

    from rules.engine import RuleEngine
    engine = RuleEngine()
    decision = engine.evaluate("帮我搜索一下最新的 AI 框架")
    # RouteDecision(action='delegate', target='researcher', ...)
"""

from rules.engine import RuleEngine
from rules.models import Rule, RouteDecision, ActionType
from rules.facts import FactRegistry

__all__ = ["RuleEngine", "Rule", "RouteDecision", "ActionType", "FactRegistry", "register_builtins"]
