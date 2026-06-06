"""向量存储 — 基于 SQLite + JSON + 纯 Python 余弦相似度

B2: 零外部依赖，适合个人 agent 的记忆规模（<10K 条）。
使用 SQLite 存储 embedding + 文本，检索时线性扫描 + 余弦相似度排序。

性能参考（纯 Python 线性扫描）：
  - 1K 条 / 1536 维: ~5ms
  - 10K 条 / 1536 维: ~50ms
  对个人 agent 场景完全足够。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度（纯 Python，无外部依赖）。"""
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))


class VectorStore:
    """向量存储。

    支持多 namespace（如 'memory'/'episodic'/'user'），每个 namespace 独立索引。
    线程安全（写操作串行化）。
    """

    def __init__(self, db_path: str):
        self._db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vectors (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace   TEXT NOT NULL,
                    text        TEXT NOT NULL,
                    embedding   TEXT NOT NULL,  -- JSON array of floats
                    metadata    TEXT DEFAULT '{}',  -- JSON dict
                    created_at  REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_namespace ON vectors(namespace)")

    def add(self, namespace: str, text: str, embedding: list[float],
            metadata: dict | None = None) -> int:
        """添加一条向量记录，返回 id。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO vectors (namespace, text, embedding, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                (namespace, text,
                 json.dumps(embedding, ensure_ascii=False),
                 json.dumps(metadata or {}, ensure_ascii=False),
                 time.time()),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def add_batch(self, namespace: str, items: list[tuple[str, list[float], dict | None]]) -> list[int]:
        """批量添加，一个事务内完成。返回 id 列表。"""
        ids: list[int] = []
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN")
            for text, embedding, metadata in items:
                conn.execute(
                    "INSERT INTO vectors (namespace, text, embedding, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                    (namespace, text,
                     json.dumps(embedding, ensure_ascii=False),
                     json.dumps(metadata or {}, ensure_ascii=False),
                     now),
                )
                ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        return ids

    def search(self, namespace: str, query_embedding: list[float],
               top_k: int = 5) -> list[dict]:
        """在 namespace 中搜索最相似的 top_k 条记录。

        返回按 cosine_similarity 降序排列的列表：
        [{id, namespace, text, metadata, score, created_at}, ...]
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, namespace, text, embedding, metadata, created_at FROM vectors WHERE namespace = ?",
                (namespace,),
            ).fetchall()

        scored = []
        for r in rows:
            emb = json.loads(r["embedding"])
            score = cosine_similarity(query_embedding, emb)
            scored.append({
                "id": r["id"],
                "namespace": r["namespace"],
                "text": r["text"],
                "metadata": json.loads(r["metadata"]),
                "score": round(score, 6),
                "created_at": r["created_at"],
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def delete(self, id: int):
        """按 id 删除。"""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM vectors WHERE id = ?", (id,))

    def delete_namespace(self, namespace: str):
        """清空整个 namespace。"""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM vectors WHERE namespace = ?", (namespace,))

    def count(self, namespace: str | None = None) -> int:
        """统计记录数。namespace 为 None 时统计全部。"""
        with self._connect() as conn:
            if namespace:
                return conn.execute("SELECT COUNT(*) FROM vectors WHERE namespace = ?", (namespace,)).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    def list_namespaces(self) -> list[str]:
        """列出所有 namespace。"""
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT namespace FROM vectors ORDER BY namespace").fetchall()
            return [r["namespace"] for r in rows]
