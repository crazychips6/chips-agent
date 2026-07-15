"""Wiki 配置管理 — 环境变量 + 默认值

配置来源（优先级：工具参数 > 环境变量 > 默认值）：
  - CHIPS_WIKI_DIR — wiki 项目根目录
  - CHIPS_LINT_STALE_DAYS — 过期告警天数（默认 30）
  - CHIPS_LINT_CRITICAL_DAYS — 严重过期天数（默认 90）
"""

from __future__ import annotations

import os


def get_wiki_dir() -> str:
    """获取 wiki 项目根目录。"""
    return os.getenv("CHIPS_WIKI_DIR") or os.path.expanduser("~/.chips/wiki")


def get_raw_dir(wiki_dir: str | None = None) -> str:
    base = wiki_dir or get_wiki_dir()
    return os.path.join(base, "raw")


def get_wiki_pages_dir(wiki_dir: str | None = None) -> str:
    base = wiki_dir or get_wiki_dir()
    return os.path.join(base, "wiki")


def get_templates_dir(wiki_dir: str | None = None) -> str:
    base = wiki_dir or get_wiki_dir()
    return os.path.join(base, "templates")


def get_stale_days() -> int:
    return int(os.getenv("CHIPS_LINT_STALE_DAYS", "30"))


def get_critical_days() -> int:
    return int(os.getenv("CHIPS_LINT_CRITICAL_DAYS", "90"))


# Wiki 页面类型 — 目录名映射
PAGE_TYPES = {
    "concepts": "concept",
    "entities": "entity",
    "sources": "source",
    "decisions": "decision",
    "lessons": "lesson",
    "answers": "answer",
}

RAW_TYPE_MAP = {
    "articles": "article",
    "journal": "journal",
    "papers": "paper",
}
