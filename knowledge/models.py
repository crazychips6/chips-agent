"""经验数据模型 — 结构化经验三元组

从 ReAct 轨迹中提取的可复用经验，存储在 ~/.chips/knowledge/ 目录下。

置信度等级：
  observed (0) → candidate (1) → recommended (2) → authoritative (3)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any


# 置信度等级映射
CONFIDENCE_ORDER = {
    "observed": 0,
    "candidate": 1,
    "recommended": 2,
    "authoritative": 3,
}

# 置信度图标
CONFIDENCE_ICONS = {
    "authoritative": "✅",
    "recommended": "📌",
    "candidate": "💡",
    "observed": "🔍",
}


@dataclass
class ToolStep:
    """工具调用步骤。"""
    tool: str
    args_pattern: str = ""
    result_pattern: str = ""
    is_optional: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ToolStep:
        return cls(
            tool=data.get("tool", ""),
            args_pattern=data.get("args_pattern", ""),
            result_pattern=data.get("result_pattern", ""),
            is_optional=data.get("is_optional", False),
        )


@dataclass
class Experience:
    """一条可复用的经验。"""
    id: str
    task: str

    # 触发条件
    triggers: list[str] = field(default_factory=list)
    intent: str = ""
    pattern: str = ""

    # 执行路径
    steps: list[ToolStep] = field(default_factory=list)
    summary: str = ""

    # 反面经验
    anti_patterns: list[str] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)

    # 置信度与统计
    confidence: str = "observed"
    success_count: int = 0
    fail_count: int = 0
    avg_tokens_saved: float = 0
    avg_steps: float = 0
    last_used: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not self.id:
            import hashlib
            self.id = hashlib.md5(f"{self.task}:{self.summary}".encode()).hexdigest()[:12]

    @property
    def confidence_level(self) -> int:
        return CONFIDENCE_ORDER.get(self.confidence, 0)

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.0
        return self.success_count / total

    @property
    def icon(self) -> str:
        return CONFIDENCE_ICONS.get(self.confidence, "•")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [s.to_dict() for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Experience:
        steps = [ToolStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            id=data.get("id", ""),
            task=data.get("task", ""),
            triggers=data.get("triggers", []),
            intent=data.get("intent", ""),
            pattern=data.get("pattern", ""),
            steps=steps,
            summary=data.get("summary", ""),
            anti_patterns=data.get("anti_patterns", []),
            pitfalls=data.get("pitfalls", []),
            confidence=data.get("confidence", "observed"),
            success_count=data.get("success_count", 0),
            fail_count=data.get("fail_count", 0),
            avg_tokens_saved=data.get("avg_tokens_saved", 0),
            avg_steps=data.get("avg_steps", 0),
            last_used=data.get("last_used", ""),
            created_at=data.get("created_at", ""),
        )

    def format_for_prompt(self) -> str:
        """格式化为 prompt 注入文本。"""
        lines = [f"\n{self.icon} **{self.task}** (置信度: {self.confidence})"]

        if self.summary:
            lines.append(f"  策略: {self.summary}")

        if self.anti_patterns:
            lines.append(f"  ⚠ 避免: {'; '.join(self.anti_patterns[:2])}")

        if self.steps:
            path = " → ".join(s.tool for s in self.steps[:4])
            lines.append(f"  路径: {path}")

        return "\n".join(lines)

    def update_stats(self, success: bool):
        """更新统计数据。"""
        if success:
            self.success_count += 1
        else:
            self.fail_count += 1
        self.last_used = time.strftime("%Y-%m-%dT%H:%M:%S")

    def auto_upgrade_confidence(self, min_samples: int = 5):
        """根据成功率自动升级置信度。"""
        total = self.success_count + self.fail_count
        if total < min_samples:
            return

        rate = self.success_rate
        if rate >= 0.8 and self.confidence != "authoritative":
            self.confidence = _upgrade_confidence(self.confidence)
        elif rate < 0.5 and self.confidence not in ("observed",):
            self.confidence = _downgrade_confidence(self.confidence)


def _upgrade_confidence(level: str) -> str:
    """升级置信度等级。"""
    current = CONFIDENCE_ORDER.get(level, 0)
    for name, order in CONFIDENCE_ORDER.items():
        if order == current + 1:
            return name
    return level


def _downgrade_confidence(level: str) -> str:
    """降级置信度等级。"""
    current = CONFIDENCE_ORDER.get(level, 0)
    for name, order in CONFIDENCE_ORDER.items():
        if order == current - 1:
            return name
    return level
