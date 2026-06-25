"""KnowledgeManager — 经验知识管理

知识是 agent 从 trace 中学习到的最优路径技巧，和记忆（事实）不同：
  - 记忆存事实（FTS5），由主 LLM 写入
  - 知识存技巧（JSON + git），由小模型 trace 分析 + 人工审核写入

置信度等级：
  observed (0) → candidate (1) → recommended (2) → authoritative (3)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("chips.knowledge")

_CONFIDENCE_ORDER = {
    "observed": 0,
    "candidate": 1,
    "recommended": 2,
    "authoritative": 3,
}


class KnowledgeManager:
    """经验知识管理器。

    Args:
        knowledge_dir: 知识库目录（默认 .chips/knowledge）
    """

    def __init__(self, knowledge_dir: str = ""):
        if not knowledge_dir:
            knowledge_dir = os.path.expanduser("~/.chips/knowledge")
        self._dir = Path(knowledge_dir)
        self._path = self._dir / "paths.json"
        self._entries: list[dict[str, Any]] = []
        self._load()

    # ── 读取 ──

    def _load(self):
        """从 paths.json 加载知识条目。"""
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
                self._entries = data.get("entries", [])
                logger.debug("knowledge_loaded entries=%d", len(self._entries))
            except Exception as exc:
                logger.warning("knowledge_load_failed: %s", exc)
                self._entries = []
        else:
            self._entries = []

    def match(self, user_message: str, max_results: int = 3) -> list[dict[str, Any]]:
        """匹配用户消息，返回最相关的知识条目。

        匹配规则：triggers 关键字命中 → 按置信度排序 → 取 top N。
        """
        matched = []
        for entry in self._entries:
            triggers = entry.get("triggers", [])
            if any(t in user_message for t in triggers):
                matched.append(entry)

        matched.sort(
            key=lambda e: _CONFIDENCE_ORDER.get(e.get("confidence", "observed"), 0),
            reverse=True,
        )
        return matched[:max_results]

    def format_knowledge(self, entries: list[dict[str, Any]]) -> str:
        """格式化为 prompt 注入文本。"""
        if not entries:
            return ""
        lines = ["# 经验知识"]
        for e in entries:
            icon = {
                "authoritative": "✅",
                "recommended": "📌",
                "candidate": "💡",
                "observed": "🔍",
            }
            c = e.get("confidence", "observed")
            lines.append(f"{icon.get(c, '•')} {e['task']} → {e['inject']}")
            avoid = e.get("avoid", "")
            if avoid:
                lines.append(f"   ⚠ {avoid}")
        return "\n".join(lines)
