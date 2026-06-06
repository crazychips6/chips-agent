"""MemoryStore — 三级记忆存储 (working / episodic / semantic)

三层模型：
- Working（工作记忆）：当前会话的结构化笔记，内存中，不清除不持久
- Episodic（情景记忆）：历史会话摘要，持久化到 EPISODIC.md，跨会话
- Semantic（语义记忆）：持久知识，MEMORY.md + USER.md，永久

B3：新增向量检索，取代全量 memory 注入 system prompt。
add() 时自动 embed 并存入 vector store（需配置 embedding_service）。
prefetch(query) 返回与当前上下文最相关的 top-k 条记忆。

B4：可切换的 RetrievalStrategy，方案 A = FTS5+权重+衰减，方案 B = hybrid（扩展点）。"""

from __future__ import annotations

import datetime
import os
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.embedding import EmbeddingProtocol
    from memory.retrieval import RetrievalStrategy
    from memory.vector import VectorStore


class MemoryStore:
    def __init__(self, memory_dir: str,
                 embedding_service: EmbeddingProtocol | None = None,
                 vector_store: VectorStore | None = None,
                 retrieval_strategy: RetrievalStrategy | None = None):
        os.makedirs(memory_dir, exist_ok=True)
        self._memory_dir = memory_dir
        self._memory_file = os.path.join(memory_dir, "MEMORY.md")
        self._user_file = os.path.join(memory_dir, "USER.md")
        self._episodic_file = os.path.join(memory_dir, "EPISODIC.md")
        # 工作记忆：仅内存，不持久化
        self._working: dict[str, str] = {}
        # 初始化时冻结快照
        self._snapshot = self._read_all()
        # B3: 可选的 embedding 服务 + 向量存储
        self._embedding = embedding_service
        self._vector_store = vector_store
        # B4: 可切换的检索策略（优先于 embedding+vector）
        self._retrieval_strategy = retrieval_strategy

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
        """返回持久化快照文本（仅 semantic + episodic），供 PromptBuilder 注入。

        如果配置了 embedding_service，推荐使用 prefetch() 替代此方法，
        以获取与当前上下文最相关的记忆而非全部。"""
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

    # ── B3/B4: 检索（优先用 retrieval_strategy，降级到 embedding+vector，最后文件快照） ──

    def prefetch(self, query: str, top_k: int = 5) -> str:
        """根据当前查询文本，检索最相关的记忆。

        优先级：
          1. retrieval_strategy（如 FTS5WeightedRetrieval / HybridRetrieval）
          2. embedding_service + vector_store（纯 dense 向量检索）
          3. 文件快照全量返回（降级）
        """
        # 策略优先
        if self._retrieval_strategy is not None:
            try:
                results = self._retrieval_strategy.search(query, top_k=top_k)
                if results:
                    lines = ["（根据当前上下文检索到的相关记忆）"]
                    for r in results:
                        score = r.get("score", 0)
                        if score > 0.3:
                            label = r.get("category", "记忆")
                            lines.append(f"- [{label}] {r['text']}")
                    return "\n".join(lines)
            except Exception:
                pass  # 策略失败降级

        # 次优：dense embedding 检索
        if self._embedding and self._vector_store:
            try:
                emb = self._embedding.embed([query])[0]
                results = self._vector_store.search("memory", emb, top_k=top_k)
                if not results:
                    results = self._vector_store.search("episodic", emb, top_k=top_k)
                if results:
                    lines = ["（根据当前上下文检索到的相关记忆）"]
                    for r in results:
                        label = r.get("metadata", {}).get("source", "记忆")
                        score = r.get("score", 0)
                        if score > 0.5:
                            lines.append(f"- [{label}] {r['text']}")
                    return "\n".join(lines)
            except Exception:
                pass

        # 兜底：全量文件快照
        return self._snapshot.get("memory", "")

    # ── B3/B4: 自动索引（add 时触发，按 retrieval_strategy → embedding+vector 顺序） ──

    def _auto_index(self, text: str, category: str, metadata: dict | None = None):
        """将一段文本自动加入检索索引。

        优先级：
          1. retrieval_strategy.add_text()（如 FTS5WeightedRetrieval）
          2. embedding_service + vector_store（纯 dense）
        静默失败（不阻塞主流程）。
        """
        if not text.strip():
            return
        # 策略优先
        if self._retrieval_strategy is not None:
            try:
                weight = 0.7 if category == "memory" else 0.5
                meta = dict(metadata or {})
                meta.setdefault("category", category)
                meta.setdefault("weight", weight)
                self._retrieval_strategy.add_text(text, metadata=meta)
                return
            except Exception:
                pass
        # 降级：dense embedding
        if self._embedding and self._vector_store:
            try:
                emb = self._embedding.embed([text])[0]
                self._vector_store.add(
                    namespace=category,
                    text=text,
                    embedding=emb,
                    metadata=metadata or {"source": category},
                )
            except Exception:
                pass

    # ── CRUD ──

    def add(self, content: str, category: str = "memory") -> dict:
        """追加一条记忆。

        支持分类：
        - "memory" / "user" → 语义记忆，持久化到文件
        - "episodic" → 情景记忆，追加到 EPISODIC.md
        - "working" → 工作记忆，仅内存，格式 "key: value"

        B3/B4：memory 和 episodic 类别自动加入检索索引。
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
            # B3/B4: 自动加入检索索引
            self._auto_index(entry, category="episodic", metadata={"source": "episodic", "time": now})
            return {"status": "ok", "category": "episodic"}

        if category not in ("memory", "user"):
            return {"status": "error", "message": f"未知分类: {category}"}

        path = self._memory_file if category == "memory" else self._user_file
        existing = self._snapshot.get(category, "")
        new_content = existing + "\n" + content if existing else content
        self._atomic_write(path, new_content)
        self._snapshot[category] = new_content
        # B3/B4: 自动加入检索索引
        self._auto_index(content, category=category, metadata={"source": category})
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
