"""检索策略 — 可切换的 Retrieval Protocol

方案 A（本地）：FTS5 + 权重 + 时间衰减
方案 B（全栈）：BM25(Tavily) + embedding + 图谱 + RRF + reranker（扩展点）

MemoryStore 通过构造注入选择策略。"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class RetrievalStrategy(Protocol):
    """检索策略协议。返回按相关性排序的结果列表。"""

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """检索相关记忆。

        Returns:
            [{text, score, metadata, ...}, ...]  按 score 降序
        """

    def add_text(self, text: str, metadata: dict | None = None) -> int:
        """添加一条可检索的文本，返回 id。"""

    def remove(self, id: int) -> None:
        """按 id 删除。"""

    def count(self) -> int:
        """统计总条目。"""


# ── 方案 A: FTS5 + 权重 + 时间衰减 ──


def _compute_score(bm25_score: float, weight: float, age_days: float,
                   weight_factor: float = 0.5,
                   decay_half_life: float = 30.0) -> float:
    """组合分数 = BM25 + 权重加成 - 时间衰减。

    - weight_factor: 权重的影响力（0~1），越大权重越重要
    - decay_half_life: 衰减半衰期（天），30 天减半
    """
    # BM25 是负数（越接近 0 越好），归一化到 0~1 范围
    norm_bm25 = 1.0 / (1.0 + math.exp(bm25_score / 5.0))
    # 权重加成：weight 范围 0~1，log(1+weight) 做平滑
    weight_bonus = math.log1p(weight) * weight_factor
    # 时间衰减：半衰期模型
    decay = 0.5 ** (age_days / decay_half_life)
    raw = (norm_bm25 + weight_bonus) * decay
    return max(0.0, min(raw, 1.0))


class FTS5WeightedRetrieval:
    """方案 A: SQLite FTS5 全文检索 + 权重 + 时间衰减。

    全本地，零外部调用。存储 schema：
      - id        INTEGER PRIMARY KEY
      - content   TEXT NOT NULL         （记忆内容）
      - weight    REAL DEFAULT 0.5      （重要性权重 0~1）
      - category  TEXT DEFAULT 'memory'  （分类标记）
      - created_at REAL                 （创建时间）
      - metadata  TEXT DEFAULT '{}'      （扩展元数据）

    自动维护 FTS5 索引，写入时触发同步。
    """

    def __init__(self, db_path: str,
                 weight_factor: float = 0.5,
                 decay_half_life: float = 30.0):
        self._db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._weight_factor = weight_factor
        self._decay_half_life = decay_half_life
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS retrieval (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    content     TEXT NOT NULL,
                    weight      REAL DEFAULT 0.5,
                    category    TEXT DEFAULT 'memory',
                    metadata    TEXT DEFAULT '{}',
                    created_at  REAL NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts
                    USING fts5(content, content=retrieval, tokenize='unicode61');

                CREATE TRIGGER IF NOT EXISTS retrieval_ai AFTER INSERT ON retrieval BEGIN
                    INSERT INTO retrieval_fts(rowid, content) VALUES (new.id, new.content);
                END;

                CREATE TRIGGER IF NOT EXISTS retrieval_ad AFTER DELETE ON retrieval BEGIN
                    INSERT INTO retrieval_fts(retrieval_fts, rowid, content) VALUES('delete', old.id, old.content);
                END;

                CREATE TRIGGER IF NOT EXISTS retrieval_au AFTER UPDATE OF content ON retrieval BEGIN
                    INSERT INTO retrieval_fts(retrieval_fts, rowid, content) VALUES('delete', old.id, old.content);
                    INSERT INTO retrieval_fts(rowid, content) VALUES (new.id, new.content);
                END;
            """)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """FTS5 全文搜索 + 权重 + 时间衰减排序。"""
        if not query.strip():
            return []

        now = time.time()
        q = " OR ".join(query.strip().split())

        with self._connect() as conn:
            rows = conn.execute(
                """SELECT r.id, r.content, r.weight, r.category,
                          r.metadata, r.created_at,
                          fts.rank AS bm25_rank
                   FROM retrieval_fts fts
                   JOIN retrieval r ON fts.rowid = r.id
                   WHERE retrieval_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (q, top_k * 2),
            ).fetchall()

        scored = []
        for r in rows:
            age_days = max(0.0, (now - r["created_at"]) / 86400.0)
            score = _compute_score(
                bm25_score=r["bm25_rank"],
                weight=r["weight"],
                age_days=age_days,
                weight_factor=self._weight_factor,
                decay_half_life=self._decay_half_life,
            )
            scored.append({
                "id": r["id"],
                "text": r["content"],
                "score": round(score, 6),
                "weight": r["weight"],
                "category": r["category"],
                "metadata": json.loads(r["metadata"]),
                "created_at": r["created_at"],
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def add_text(self, text: str, metadata: dict | None = None) -> int:
        with self._lock, self._connect() as conn:
            weight = (metadata or {}).pop("weight", 0.5)
            category = (metadata or {}).pop("category", "memory")
            conn.execute(
                "INSERT INTO retrieval (content, weight, category, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                (text, weight, category,
                 json.dumps(metadata or {}, ensure_ascii=False),
                 time.time()),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def remove(self, id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM retrieval WHERE id = ?", (id,))

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM retrieval").fetchone()[0]


# ── 方案 B: Hybrid (BM25 + embedding + graph + RRF + reranker) ──
#
# 扩展点。初始化时注入外部依赖（Tavily SDK、reranker client、图谱存储等）。
# 检索时会合并多个信号源，用 RRF 融合排序，最后由 reranker 做精排。
#
# 待 Plan B 实现后补全。


class HybridRetrieval:
    """方案 B 骨架 — BM25(Tavily) + embedding + 图谱 + RRF + reranker。

    TODO: 完整实现需要以下依赖：
      - Tavily API（web/知识检索）
      - 知识图谱存储（如 NetworkX + SQLite）
      - Cross-encoder reranker（本地 BGE 或云端 Cohere）
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        raise NotImplementedError("HybridRetrieval 尚未实现")

    def add_text(self, text: str, metadata: dict | None = None) -> int:
        raise NotImplementedError("HybridRetrieval 尚未实现")

    def remove(self, id: int) -> None:
        raise NotImplementedError("HybridRetrieval 尚未实现")

    def count(self) -> int:
        raise NotImplementedError("HybridRetrieval 尚未实现")
