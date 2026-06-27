"""AgentRegistry — agents.yaml 定义的角色 Agent 管理

提供按名查询、列表、注册、注销、更新功能。
运行时 CRUD 自动持久化到 YAML，零内部依赖。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

_AGENTS_PATH = Path.home() / ".chips" / "agents.yaml"

# Agent 配置的字段定义（含默认值），用于校验和补全
_AGENT_FIELDS: dict[str, tuple[type, Any]] = {
    "description": (str, ""),
    "model": (str, ""),
    "tools": (list, []),
    "system_prompt": (str, ""),
    "max_iterations": (int, 10),
    "pool_size": (int, 5),
}


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
            pool_size: 5
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

    def _save(self) -> None:
        """原子写入 agents.yaml。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"agents": self._agents}
        fd, tmp = tempfile.mkstemp(suffix=".yaml", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            os.replace(tmp, str(self._path))
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ── CRUD ──

    def register(self, name: str, definition: dict[str, Any]) -> dict[str, Any]:
        """注册一个新 Agent 角色。已存在则更新。

        Args:
            name: 角色名（字母数字+下划线）
            definition: 配置字典，支持 description/model/tools/system_prompt/
                       max_iterations/pool_size，缺省字段用默认值补全

        Returns:
            补全默认值后的完整配置

        Raises:
            ValueError: name 格式不合法
        """
        if not name or not isinstance(name, str):
            raise ValueError("角色名不能为空")
        if not name.replace("_", "").isalnum():
            raise ValueError("角色名只能包含字母、数字和下划线")

        entry = dict(definition)
        # 补全缺省字段
        for field, (ftype, default) in _AGENT_FIELDS.items():
            if field not in entry:
                entry[field] = default
            elif not isinstance(entry[field], ftype):
                entry[field] = default
        # tools 保证是列表
        if isinstance(entry.get("tools"), str):
            entry["tools"] = [t.strip() for t in entry["tools"].split(",") if t.strip()]

        self._agents[name] = entry
        self._save()
        return dict(entry)

    def unregister(self, name: str) -> bool:
        """注销一个 Agent 角色。

        Returns:
            True 成功删除，False 不存在
        """
        if name not in self._agents:
            return False
        del self._agents[name]
        self._save()
        return True

    def update(self, name: str, definition: dict[str, Any]) -> dict[str, Any] | None:
        """更新 Agent 配置。不存在的字段保持原值。

        Returns:
            更新后的完整配置，不存在返回 None
        """
        if name not in self._agents:
            return None
        entry = self._agents[name]
        for k, v in definition.items():
            if v is not None and k in _AGENT_FIELDS:
                ftype = _AGENT_FIELDS[k][0]
                if isinstance(v, ftype):
                    entry[k] = v
        if isinstance(entry.get("tools"), str):
            entry["tools"] = [t.strip() for t in entry["tools"].split(",") if t.strip()]
        self._save()
        return dict(entry)

    # ── 查询 ──

    def get(self, name: str) -> dict[str, Any] | None:
        """按名查询 Agent 定义，不存在返回 None。返回副本。"""
        entry = self._agents.get(name)
        if entry is None:
            return None
        return dict(entry)

    def list(self) -> list[dict[str, Any]]:
        """列出所有已注册 Agent（不含 system_prompt，太长）。"""
        result = []
        for name, entry in self._agents.items():
            item = {"name": name}
            for field in ("description", "model", "tools", "max_iterations"):
                if field in entry:
                    item[field] = entry[field]
            result.append(item)
        return result

    @property
    def names(self) -> list[str]:
        return list(self._agents.keys())

    def __bool__(self) -> bool:
        return bool(self._agents)

    def reload(self) -> None:
        self._load()
