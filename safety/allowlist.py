"""审批白名单 — 持久化用户批准的 dangerous 命令。

存入 ~/.chips/allowlist.yaml，格式：
  allowlist:
    - command: "sudo apt update"
      pattern: "sudo"
      added_at: 2026-06-04T12:00:00

零项目内部依赖。"""

import os
import time
from pathlib import Path

_ALLOWLIST_PATH = Path(os.environ.get("CHIPS_ALLOWLIST_PATH") or Path.home() / ".chips" / "allowlist.yaml")


def check(command: str) -> bool:
    """检查命令是否在白名单中（精确字符串匹配）。"""
    entries = _load()
    return any(e["command"] == command for e in entries)


def add(command: str, pattern: str = ""):
    """将命令加入白名单（重复条目不重复添加）。"""
    entries = _load()
    if any(e["command"] == command for e in entries):
        return
    entries.append({
        "command": command,
        "pattern": pattern,
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    _save(entries)


def remove(command: str) -> bool:
    """从白名单中移除指定命令。"""
    entries = _load()
    new_entries = [e for e in entries if e["command"] != command]
    if len(new_entries) == len(entries):
        return False
    _save(new_entries)
    return True


def clear():
    """清空白名单。"""
    _save([])


def list_all() -> list[dict]:
    """列出所有白名单条目。"""
    return _load()


def count() -> int:
    return len(_load())


def _load() -> list[dict]:
    if not _ALLOWLIST_PATH.exists():
        return []
    try:
        import yaml
        with open(_ALLOWLIST_PATH) as f:
            data = yaml.safe_load(f) or {}
        return data.get("allowlist", [])
    except Exception:
        return []


def _save(entries: list[dict]):
    import yaml
    _ALLOWLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_ALLOWLIST_PATH, "w") as f:
        yaml.dump({"allowlist": entries}, f, default_flow_style=False, allow_unicode=True)
