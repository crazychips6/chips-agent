"""MemoryStore — 三级记忆存储 (working / episodic / semantic)

三层模型：
- Working（工作记忆）：当前会话的结构化笔记，内存中，不清除不持久
- Episodic（情景记忆）：历史会话摘要，持久化到 EPISODIC.md，跨会话
- Semantic（语义记忆）：持久知识，MEMORY.md + USER.md，永久

快照在 __init__ 时冻结，add() 更新快照并原子写回磁盘。"""

import datetime
import os
import tempfile


class MemoryStore:
    def __init__(self, memory_dir: str):
        os.makedirs(memory_dir, exist_ok=True)
        self._memory_dir = memory_dir
        self._memory_file = os.path.join(memory_dir, "MEMORY.md")
        self._user_file = os.path.join(memory_dir, "USER.md")
        self._episodic_file = os.path.join(memory_dir, "EPISODIC.md")
        # 工作记忆：仅内存，不持久化
        self._working: dict[str, str] = {}
        # 初始化时冻结快照
        self._snapshot = self._read_all()

    def _read_file(self, path: str) -> str:
        if os.path.exists(path):
            with open(path) as f:
                return f.read().strip()
        return ""

    def _read_all(self) -> dict[str, str]:
        return {
            "memory": self._read_file(self._memory_file),
            "user": self._read_file(self._user_file),
            "episodic": self._read_file(self._episodic_file),
        }

    def _atomic_write(self, path: str, content: str):
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp, path)
        except Exception:
            os.unlink(tmp)
            raise

    def for_system_prompt(self) -> str:
        """返回持久化快照文本（仅 semantic + episodic），供 PromptBuilder 注入。"""
        parts = []
        if self._snapshot.get("memory"):
            parts.append(f"## 持久记忆\n{self._snapshot['memory']}")
        if self._snapshot.get("user"):
            parts.append(f"## 关于用户\n{self._snapshot['user']}")
        if self._snapshot.get("episodic"):
            parts.append(f"## 历史会话摘要\n{self._snapshot['episodic']}")
        return "\n\n".join(parts)

    def get_all(self) -> dict[str, str]:
        """返回全部快照 + 工作记忆。"""
        return {
            **dict(self._snapshot),
            "working": dict(self._working),
        }

    def add(self, content: str, category: str = "memory") -> dict:
        """追加一条记忆。

        支持分类：
        - "memory" / "user" → 语义记忆，持久化到文件
        - "episodic" → 情景记忆，追加到 EPISODIC.md
        - "working" → 工作记忆，仅内存，格式 "key: value"
        """
        if category == "working":
            if ":" in content:
                key, value = content.split(":", 1)
                self._working[key.strip()] = value.strip()
            else:
                self._working[content] = ""
            return {"status": "ok", "category": "working"}

        if category == "episodic":
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = f"- [{now}] {content}"
            existing = self._snapshot.get("episodic", "")
            new_content = existing + "\n" + entry if existing else entry
            self._atomic_write(self._episodic_file, new_content)
            self._snapshot["episodic"] = new_content
            return {"status": "ok", "category": "episodic"}

        if category not in ("memory", "user"):
            return {"status": "error", "message": f"未知分类: {category}"}

        path = self._memory_file if category == "memory" else self._user_file
        existing = self._snapshot.get(category, "")
        new_content = existing + "\n" + content if existing else content
        self._atomic_write(path, new_content)
        self._snapshot[category] = new_content
        return {"status": "ok", "category": category, "file": path}

    # ── Working Memory 管理 ──

    def set_working(self, key: str, value: str):
        self._working[key] = value

    def get_working(self, key: str = "") -> dict | str:
        if key:
            return self._working.get(key, "")
        return dict(self._working)

    def clear_working(self):
        self._working.clear()

    # ── Episodic 管理 ──

    def summarize_to_episodic(self, summary: str):
        """将当前会话摘要写入 episodic 记忆。"""
        self.add(summary, category="episodic")
