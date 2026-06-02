"""SessionDB — 会话持久化

SQLite WAL + FTS5 实现对话历史存储和全文检索。
零项目内部依赖。"""

import json
import os
import sqlite3
import threading
import time


class SessionDB:
    """会话数据库。

    自动创建 sessions 和 messages 表 + FTS5 全文索引。
    线程安全（每个连接独立 cursor，写操作串行化）。
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
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    title       TEXT DEFAULT '',
                    system_prompt TEXT DEFAULT '',
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role        TEXT NOT NULL,
                    content     TEXT DEFAULT '',
                    tool_calls  TEXT DEFAULT '[]',
                    created_at  REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, id);

                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                    USING fts5(content, content=messages, tokenize='unicode61');

                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                END;

                CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
                END;

                CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF content ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
                    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                END;
            """)

    # ── Session CRUD ──

    def create_session(self, title: str = "", system_prompt: str = "") -> str:
        """创建新会话，返回 session_id。"""
        now = time.time()
        session_id = self._generate_id(now)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, system_prompt, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, title, system_prompt, now, now),
            )
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """列出最近的会话，按 updated_at 降序。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at, "
                "(SELECT COUNT(*) FROM messages WHERE session_id = sessions.id) AS msg_count "
                "FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str):
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # ── Message CRUD ──

    def save_message(self, session_id: str, role: str, content: str,
                     tool_calls: list | None = None):
        """保存一条消息到会话。"""
        now = time.time()
        tool_calls_json = json.dumps(tool_calls or [], ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, tool_calls_json, now),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    def save_messages(self, session_id: str, messages: list[dict]):
        """批量保存消息，一个事务内完成（原子）。"""
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN")
            for msg in messages:
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?, ?, ?, ?, ?)",
                    (session_id, msg["role"], msg.get("content", ""),
                     json.dumps(msg.get("tool_calls", []), ensure_ascii=False),
                     msg.get("created_at", now)),
                )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    def get_history(self, session_id: str) -> list[dict]:
        """获取会话的全部消息历史，按 id 升序。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, tool_calls, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()

        history = []
        for r in rows:
            msg = {"role": r["role"], "content": r["content"]}
            tcs = json.loads(r["tool_calls"])
            if tcs:
                msg["tool_calls"] = tcs
            history.append(msg)
        return history

    # ── Search ──

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """全文搜索消息内容，返回匹配的消息及其会话信息。"""
        # FTS5 要求转义特殊字符
        q = " OR ".join(query.strip().split())
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT m.session_id, s.title AS session_title, "
                "m.role, m.content, m.created_at "
                "FROM messages_fts f "
                "JOIN messages m ON f.rowid = m.id "
                "JOIN sessions s ON m.session_id = s.id "
                "WHERE messages_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (q, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Helpers ──

    @staticmethod
    def _generate_id(now: float) -> str:
        import random
        ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        suffix = f"{random.randrange(1000, 9999)}"  # noqa: S311
        return f"{ts}-{suffix}"
