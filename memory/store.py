"""MemoryStore — 冻结快照 + 原子写

双文件模型（MEMORY.md + USER.md），按 category 分区存储。
快照在 __init__ 时冻结，add() 更新快照并原子写回磁盘。"""

import os
import tempfile


class MemoryStore:
    def __init__(self, memory_dir: str):
        os.makedirs(memory_dir, exist_ok=True)
        self._memory_file = os.path.join(memory_dir, "MEMORY.md")
        self._user_file = os.path.join(memory_dir, "USER.md")
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
        """返回冻结快照文本，供 PromptBuilder 注入 system prompt。"""
        parts = []
        if self._snapshot["memory"]:
            parts.append(f"## 记忆\n{self._snapshot['memory']}")
        if self._snapshot["user"]:
            parts.append(f"## 关于用户\n{self._snapshot['user']}")
        return "\n\n".join(parts)

    def get_all(self) -> dict[str, str]:
        """返回原始快照 dict，供 PromptBuilder 分 layer 注入。"""
        return dict(self._snapshot)

    def add(self, content: str, category: str = "memory") -> dict:
        """追加记忆并原子写回，同时更新内存快照。"""
        if category not in ("memory", "user"):
            return {"status": "error", "message": f"未知分类: {category}"}

        path = self._memory_file if category == "memory" else self._user_file
        existing = self._snapshot[category]
        new_content = existing + "\n" + content if existing else content

        self._atomic_write(path, new_content)
        self._snapshot[category] = new_content

        return {"status": "ok", "category": category, "file": path}
