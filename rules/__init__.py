"""rules — 兼容层（已迁移到 safety/ 和 endpoint/）

新代码应直接导入：
  from safety.guard import GuardEngine
  from safety.models import Rule, RouteDecision
  from endpoint.fast_llm import FastLLM
"""

from safety.guard import GuardEngine
from safety.models import Rule, RouteDecision
from endpoint.fast_llm import FastLLM

__all__ = ["GuardEngine", "Rule", "RouteDecision", "FastLLM"]
