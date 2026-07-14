"""分类结果数据模型 — 统一四层分类的返回格式

每层分类结果包含：
  - intent: 意图名
  - confidence: 置信度 (0-1)
  - priority: 层级优先级（规则 > 上下文 > 语义 > LLM）
  - source: 来源标识
  - predicted_tools: 预测工具列表
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 层级优先级定义（越靠前越"确定"）
PRIORITY_RULE = 4      # 规则匹配：人工定义的确定性逻辑
PRIORITY_CONTEXT = 3   # 上下文匹配：基于历史意图
PRIORITY_SEMANTIC = 2  # 语义检索：基于向量相似度
PRIORITY_LLM = 1       # LLM 分类：模型"猜测"
PRIORITY_NONE = 0      # 无结果


@dataclass
class ClassifyResult:
    """单层分类结果。"""
    intent: str
    confidence: float  # 0-1
    priority: int      # 层级优先级
    source: str        # rule / context / semantic / llm
    predicted_tools: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_high_confidence(self) -> bool:
        """高置信度（可直接采信，无需仲裁）。"""
        return self.confidence >= 0.95

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "priority": self.priority,
            "source": self.source,
            "predicted_tools": self.predicted_tools,
            "candidates": self.candidates,
        }


# ── 仲裁器 ──


def arbitrate(results: list[ClassifyResult]) -> ClassifyResult | None:
    """从多层结果中仲裁出最终意图。

    规则：
    1. 高置信度（>= 0.95）直接采信，提前退出
    2. 按优先级排序，优先级相同取置信度最高的
    3. 关键原则：规则 0.7 > LLM 0.8（规则更可靠）
    """
    if not results:
        return None

    # 1. 检查高置信度结果
    for r in results:
        if r.is_high_confidence:
            return r

    # 2. 按优先级降序、置信度降序排序
    sorted_results = sorted(results, key=lambda r: (r.priority, r.confidence), reverse=True)

    # 3. 返回优先级最高的
    return sorted_results[0]


def merge_results(
    rule_result: ClassifyResult | None,
    context_result: ClassifyResult | None,
    semantic_result: ClassifyResult | None,
    llm_result: ClassifyResult | None,
) -> ClassifyResult | None:
    """合并四层结果并仲裁。"""
    results = [r for r in [rule_result, context_result, semantic_result, llm_result] if r]
    return arbitrate(results)
