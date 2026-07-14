"""意图配置加载器 — 从 YAML 文件动态加载意图定义

设计原则：
  - 新增意图只需在 agent/intents/ 目录下添加 YAML 文件
  - 不修改任何 Python 代码
  - 支持热重载（reload 方法）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("chips.agent.intent_loader")

INTENTS_DIR = Path(__file__).parent / "intents"


class IntentDef:
    """单个意图的定义。"""

    def __init__(self, data: dict[str, Any]):
        self.name: str = data["name"]
        self.model: str = data.get("model", "large")
        self.tools: list[str] | str = data.get("tools", "all")
        self.keywords: list[str] = data.get("keywords", [])
        self.time_sensitive: bool = data.get("time_sensitive", False)
        self.prompt_hint: str = data.get("prompt_hint", "")

    def to_route(self) -> dict[str, Any]:
        """转为路由表格式。"""
        return {"model": self.model, "tools": self.tools}


class IntentRegistry:
    """意图注册表 — 从 YAML 文件加载所有意图定义。"""

    def __init__(self):
        self._intents: dict[str, IntentDef] = {}
        self._load_all()

    def _load_all(self):
        """加载 intents 目录下所有 YAML 文件。"""
        if not INTENTS_DIR.is_dir():
            logger.warning("intents_dir_not_found: %s", INTENTS_DIR)
            return

        for yaml_file in sorted(INTENTS_DIR.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if not data or "name" not in data:
                    continue
                intent = IntentDef(data)
                self._intents[intent.name] = intent
            except Exception as e:
                logger.warning("intent_load_failed file=%s error=%s", yaml_file.name, e)

        logger.info("intents_loaded count=%d names=%s", len(self._intents), list(self._intents.keys()))

    def reload(self):
        """热重载所有意图配置。"""
        self._intents.clear()
        self._load_all()

    def get(self, name: str) -> IntentDef | None:
        return self._intents.get(name)

    def get_all(self) -> dict[str, IntentDef]:
        return dict(self._intents)

    def get_route_table(self) -> dict[str, dict[str, Any]]:
        """构建路由表（兼容旧的 INTENT_ROUTES 格式）。"""
        return {name: intent.to_route() for name, intent in self._intents.items()}

    def get_tools_for_intent(self, name: str) -> list[str] | str:
        """获取 intent 对应的可见工具列表。"""
        intent = self._intents.get(name)
        if intent is None:
            intent = self._intents.get("other")
        if intent is None:
            return "all"
        return intent.tools

    def get_all_keywords(self) -> dict[str, list[str]]:
        """获取所有意图的关键词（供分类 prompt 构建）。"""
        return {name: intent.keywords for name, intent in self._intents.items() if intent.keywords}

    def get_prompt_hint(self, name: str) -> str:
        """获取意图的 prompt hint。"""
        intent = self._intents.get(name)
        return intent.prompt_hint if intent else ""

    def is_time_sensitive(self, name: str) -> bool:
        """检查意图是否时间敏感。"""
        intent = self._intents.get(name)
        return intent.time_sensitive if intent else False


# 模块级单例
intent_registry = IntentRegistry()
