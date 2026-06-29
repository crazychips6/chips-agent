"""GuardEngine — 安全拦截引擎

职责：
  1. 加载安全规则（YAML → Rule 对象列表）
  2. 对用户消息做关键字子串匹配
  3. 命中 → block，未命中 → direct（放行进 ReAct）

不做的：
  - 不分发任务给子 Agent
  - 不做意图分类
  - 不做工具推荐
"""

from __future__ import annotations

import logging
from pathlib import Path

from safety.loader import load_rules
from safety.models import LeafCondition, GroupCondition, RouteDecision

logger = logging.getLogger("chips.safety.guard")


class GuardEngine:
    """安全拦截引擎。

    只做一件事：判断一条消息是否需要拦截（block）。
    不需要拦截的消息统一返回 direct，放行进 ReAct。
    """

    def __init__(
        self,
        user_rules_path: str | Path | None = None,
        local_rules_path: str | Path | None = None,
        include_defaults: bool = True,
    ):
        self._rules = load_rules(
            user_path=Path(user_rules_path) if user_rules_path else None,
            local_path=Path(local_rules_path) if local_rules_path else None,
            include_defaults=include_defaults,
        )
        # 只保留 block 规则
        self._block_rules = [r for r in self._rules if r.then == "block"]
        self._stats = {"evaluations": 0, "blocks": 0}

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    @property
    def rules(self) -> list:
        return list(self._rules)

    def reload(self) -> None:
        """重新加载规则（运行时修改 routing.yaml 后调用）。"""
        self._rules = load_rules(include_defaults=True)
        self._block_rules = [r for r in self._rules if r.then == "block"]

    # ── 主入口 ──

    def evaluate(self, text: str) -> RouteDecision:
        """判断消息是否需要拦截。

        Args:
            text: 用户输入文本

        Returns:
            block — 需要拦截
            direct — 放行
        """
        self._stats["evaluations"] += 1

        text_lower = text.lower()

        for rule in self._block_rules:
            if self._match_rule(rule, text_lower):
                self._stats["blocks"] += 1
                return RouteDecision(
                    action="block",
                    reason=rule.reason or f"命中规则: {rule.name}",
                    matched_rule=rule.name,
                )

        return RouteDecision(action="direct", reason="未命中拦截规则")

    @staticmethod
    def _match_rule(rule, text_lower: str) -> bool:
        """对 block 规则做关键字子串匹配。

        比 keyword-extraction + set-intersection 更可靠：
        - 中文无需分词
        - 支持包含特殊字符的短语（如 "rm -rf /"）
        """
        if rule.condition is None:
            return True

        # 从 LeafCondition 提取关键字列表做子串匹配
        keywords = _collect_keywords(rule.condition)
        return any(kw in text_lower for kw in keywords)

    def summary(self) -> str:
        total = self._stats["evaluations"]
        if total == 0:
            return "GuardEngine: 尚无评估记录"
        blocks = self._stats["blocks"]
        return (
            f"GuardEngine 统计 (共 {total} 次评估):\n"
            f"  拦截: {blocks} ({blocks/total*100:.1f}%)\n"
            f"  放行: {total - blocks} ({(total-blocks)/total*100:.1f}%)"
        )


def _collect_keywords(condition) -> list[str]:
    """从条件树中递归收集所有 in/eq/match 操作的目标值（用于子串匹配）。"""
    if isinstance(condition, LeafCondition):
        if condition.op in ("in",):
            if isinstance(condition.value, (list, tuple)):
                return [str(v).lower() for v in condition.value if v is not None]
        elif condition.op == "eq" and condition.value is not None:
            return [str(condition.value).lower()]
        elif condition.op == "contains" and condition.value is not None:
            return [str(condition.value).lower()]
        return []
    elif isinstance(condition, GroupCondition):
        results = []
        for child in condition.children:
            results.extend(_collect_keywords(child))
        return results
    return []
