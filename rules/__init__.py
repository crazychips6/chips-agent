"""Agent 路由规则引擎

三层递进判断：规则引擎 → LLM Router → Fallback。

用法::

    from rules.engine import RuleEngine
    engine = RuleEngine()
    decision = engine.evaluate("帮我搜索一下最新的 AI 框架")
    # RouteDecision(action='delegate', target='researcher', ...)

    from rules.llm_router import LLMRouter
    router = LLMRouter(gateway=gw, agent_registry=reg)
    decision = router.route("复杂任务")
    # RouteDecision(action='delegate', target='researcher', ...)
"""

from rules.engine import RuleEngine
from rules.models import Rule, RouteDecision, ActionType
from rules.facts import FactRegistry
from rules.llm_router import LLMRouter

__all__ = ["RuleEngine", "LLMRouter", "Rule", "RouteDecision", "ActionType", "FactRegistry"]
