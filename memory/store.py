"""MemoryStore — 文件级持久记忆（Hermes 风格）

三级存储：
  - memory:   MEMORY.md   agent 的持久笔记
  - user:     USER.md     关于用户的持久信息
  - episodic: EPISODIC.md 历史会话摘要（带时间戳）

初始化时全部读入内存。get_memory/get_user/get_episodic 返回当前内容供 system prompt。
写入通过 add()/replace()/remove()，每次写后同步磁盘，返回完整条目列表 + 使用量统计。

零内部依赖。"""

import datetime
import os
import re
import tempfile

ENTRY_DELIMITER = "\n§\n"

# ── 注入检测 ──

_INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|directives|commands|rules)", "prompt_injection"),
    (r"you\s+are\s+(now\s+)?(a\s+)?(new\s+)?(system|assistant)", "role_hijack"),
    (r"(forget|disregard)\s+(all\s+)?(previous|prior)\s+(instructions|directives|rules)", "forget_instruction"),
    (r"system\s*(prompt\s*)?(override|reset)", "sys_override"),
]

_INVISIBLE_CHARS = {
    "​", "‌", "‍", "⁠", "﻿",
    "‪", "‫", "‬", "‭", "‮",
}


def _scan_injection(content: str) -> str | None:
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"内容包含不可见 Unicode 字符 U+{ord(char):04X}"
    for pattern, pid in _INJECTION_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return f"内容匹配威胁模式 '{pid}'"
    return None


class MemoryStore:
    SNAPSHOT_KEYS = ("memory", "user", "episodic")

    def __init__(self, memory_dir: str,
                 memory_char_limit: int = 2200,
                 user_char_limit: int = 1375):
        os.makedirs(memory_dir, exist_ok=True)
        self._dir = memory_dir
        self._limits = {"memory": memory_char_limit, "user": user_char_limit, "episodic": 0}
        self._files = {k: os.path.join(memory_dir, f"{k.upper()}.md") for k in self.SNAPSHOT_KEYS}
        self._entries: dict[str, list[str]] = {k: [] for k in self.SNAPSHOT_KEYS}
        self._load_all()

    # ── System Prompt 接口 ──

    def get_memory(self) -> str:
        """MEMORY.md 内容（条目用分隔符连接）。"""
        return ENTRY_DELIMITER.join(self._entries["memory"])

    def get_user(self) -> str:
        """USER.md 内容。"""
        return ENTRY_DELIMITER.join(self._entries["user"])

    def get_episodic(self) -> str:
        """EPISODIC.md 内容。"""
        return ENTRY_DELIMITER.join(self._entries["episodic"])

    def for_system_prompt(self) -> str:
        """返回三段内容拼合的格式化文本（兼容旧接口 / tool 用）。"""
        parts = []
        if self.get_memory():
            parts.append(f"## 持久记忆\n{self.get_memory()}")
        if self.get_user():
            parts.append(f"## 关于用户\n{self.get_user()}")
        if self.get_episodic():
            parts.append(f"## 历史会话摘要\n{self.get_episodic()}")
        return "\n\n".join(parts)

    # ── 内部 IO ──

    def _load_all(self):
        for key in self.SNAPSHOT_KEYS:
            self._entries[key] = self._read_entries(self._files[key])

    @staticmethod
    def _read_entries(path: str) -> list[str]:
        if not os.path.exists(path):
            return []
        try:
            raw = open(path, encoding="utf-8").read().strip()
        except OSError:
            return []
        if not raw:
            return []
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    def _write_entries(self, target: str):
        content = ENTRY_DELIMITER.join(self._entries[target]) if self._entries[target] else ""
        path = self._files[target]
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── CRUD ──

    def add(self, target: str, content: str) -> dict:
        """追加条目。返回 {"success", "entries", "usage", "entry_count", "message"}。"""
        if target not in self.SNAPSHOT_KEYS:
            return {"success": False, "error": f"未知分类: {target}"}
        content = content.strip()
        if not content:
            return {"success": False, "error": "内容不能为空"}

        err = _scan_injection(content)
        if err:
            return {"success": False, "error": err}

        if target == "episodic":
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            content = f"[{now}] {content}"

        entries = self._entries[target]
        if content in entries:
            return self._success(target, "条目已存在（未重复添加）")

        limit = self._limits.get(target)
        if limit:
            current_total = len(ENTRY_DELIMITER.join(entries)) if entries else 0
            add_cost = (len(ENTRY_DELIMITER) if entries else 0) + len(content)
            if current_total + add_cost > limit:
                return {
                    "success": False,
                    "error": f"超出字符限制 ({current_total}/{limit})",
                    "entries": list(entries),
                    "usage": f"{current_total}/{limit}",
                }

        entries.append(content)
        self._write_entries(target)
        return self._success(target, "已添加")

    def replace(self, target: str, old_text: str, new_content: str) -> dict:
        """替换包含 old_text 的条目。仅支持 memory/user。"""
        if target not in ("memory", "user"):
            return {"success": False, "error": f"不支持替换 {target}"}
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text 不能为空"}
        if not new_content:
            return {"success": False, "error": "new_content 不能为空"}

        err = _scan_injection(new_content)
        if err:
            return {"success": False, "error": err}

        entries = self._entries[target]
        matches = [(i, e) for i, e in enumerate(entries) if old_text in e]
        if not matches:
            return {"success": False, "error": f"未找到包含 '{old_text}' 的条目"}
        if len(matches) > 1:
            return {"success": False, "error": "多条条目匹配，请使用更精确的文本"}

        idx = matches[0][0]
        entries[idx] = new_content
        self._write_entries(target)
        return self._success(target, "已替换")

    def remove(self, target: str, old_text: str) -> dict:
        """删除包含 old_text 的条目。仅支持 memory/user。"""
        if target not in ("memory", "user"):
            return {"success": False, "error": f"不支持删除 {target}"}
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text 不能为空"}

        entries = self._entries[target]
        matches = [(i, e) for i, e in enumerate(entries) if old_text in e]
        if not matches:
            return {"success": False, "error": f"未找到包含 '{old_text}' 的条目"}
        if len(matches) > 1:
            return {"success": False, "error": "多条条目匹配，请使用更精确的文本"}

        idx = matches[0][0]
        entries.pop(idx)
        self._write_entries(target)
        return self._success(target, "已删除")

    def summarize_to_episodic(self, summary: str):
        """会话结束自动摘要写入。"""
        self.add("episodic", summary)

    # ── 响应构建 ──

    def _success(self, target: str, message: str = "") -> dict:
        entries = list(self._entries[target])
        total = len(ENTRY_DELIMITER.join(entries)) if entries else 0
        limit = self._limits.get(target, 0)
        pct = min(100, int(total / limit * 100)) if limit > 0 else 0
        usage = f"{pct}% — {total}/{limit} 字符" if limit else f"{total} 字符"
        resp: dict = {
            "success": True,
            "entries": entries,
            "usage": usage,
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp
