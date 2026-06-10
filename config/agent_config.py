"""AgentRegistry — agents.yaml 定义的角色 Agent 管理

读取 ~/.chips/agents.yaml，提供按名查询和列表功能。
零内部依赖，纯配置层。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_AGENTS_PATH = Path.home() / ".chips" / "agents.yaml"


class AgentRegistry:
    """角色 Agent 注册表。

    agents.yaml 格式:
        agents:
          researcher:
            description: "研究助手"
            model: deepseek-chat
            tools: [web]
            system_prompt: "..."
            max_iterations: 20
    """

    def __init__(self, path: Path | None = None):
        self._path = path or _AGENTS_PATH
        self._agents: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._agents = {}
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._agents = data.get("agents", {})
        except Exception:
            self._agents = {}

    def get(self, name: str) -> dict[str, Any] | None:
        """按名查询 Agent 定义，不存在返回 None。"""
        entry = self._agents.get(name)
        if entry is None:
            return None
        # 返回副本，防止外部修改
        return dict(entry)

    def list(self) -> list[dict[str, Any]]:
        """列出所有已注册 Agent。"""
        return [
            {
                "name": name,
                "description": entry.get("description", ""),
                "model": entry.get("model", ""),
                "tools": entry.get("tools", []),
            }
            for name, entry in self._agents.items()
        ]

    @property
    def names(self) -> list[str]:
        return list(self._agents.keys())

    def __bool__(self) -> bool:
        return bool(self._agents)

    def reload(self) -> None:
        self._load()
