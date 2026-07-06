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
                    parent_session_id TEXT REFERENCES sessions(id),
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

                CREATE TABLE IF NOT EXISTS tool_call_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    turn_number     INTEGER NOT NULL DEFAULT 0,
                    tool_name       TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'success',
                    duration_ms     REAL DEFAULT 0.0,
                    error_message   TEXT DEFAULT '',
                    created_at      REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tool_call_session
                    ON tool_call_log(session_id, id);

                CREATE TABLE IF NOT EXISTS usage_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    model           TEXT NOT NULL,
                    prompt_tokens   INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    latency_ms      INTEGER NOT NULL DEFAULT 0,
                    cost_estimate   REAL DEFAULT 0.0,
                    created_at      REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_usage_session
                    ON usage_log(session_id, id);

                CREATE TABLE IF NOT EXISTS sub_agents (
                    id              TEXT PRIMARY KEY,
                    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    parent_id       TEXT,
                    agent_name      TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    task            TEXT DEFAULT '',
                    output          TEXT DEFAULT '',
                    error           TEXT DEFAULT '',
                    prompt_tokens   INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    tool_calls      TEXT DEFAULT '[]',
                    started_at      REAL NOT NULL,
                    completed_at    REAL
                );

                CREATE INDEX IF NOT EXISTS idx_sub_agents_session
                    ON sub_agents(session_id, status);

                CREATE TABLE IF NOT EXISTS sub_agent_events (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    sub_agent_id    TEXT NOT NULL REFERENCES sub_agents(id) ON DELETE CASCADE,
                    from_status     TEXT NOT NULL,
                    to_status       TEXT NOT NULL,
                    timestamp       REAL NOT NULL,
                    metadata        TEXT DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_sub_agent_events_id
                    ON sub_agent_events(sub_agent_id, id);
            """)

    # ── Session CRUD ──

    def create_session(self, title: str = "", system_prompt: str = "",
                       parent_session_id: str | None = None) -> str:
        """创建新会话，返回 session_id。

        Args:
            title: 会话标题
            system_prompt: 使用的 system prompt
            parent_session_id: 父会话 ID（子 Agent 创建会话时关联主会话）
        """
        now = time.time()
        session_id = self._generate_id(now)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, system_prompt, parent_session_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, title, system_prompt, parent_session_id, now, now),
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

    # ── Content 序列化（支持 ContentBlock 多模态内容） ──

    @staticmethod
    def _serialize_content(content: str | list) -> str:
        """将 content 序列化为可存储的字符串。

        str → 原样返回（清洗 surrogate 字符）
        list[dict] → JSON
        list[dataclass 对象]（TextBlock/ImageBlock）→ 先转 dict 再 JSON

        注意：在写入层统一清洗 surrogate，避免下游 UnicodeEncodeError。
        """
        if isinstance(content, str):
            # 清洗 surrogate 字符（如 DeepSeek 输出的 \udce4）
            return content.encode("utf-8", errors="replace").decode("utf-8")
        # 将 ContentBlock dataclass 对象转为 dict
        dicts = []
        for block in content:
            if isinstance(block, dict):
                dicts.append(block)
            elif hasattr(block, "type"):
                if block.type == "text":
                    dicts.append({"type": "text", "text": block.text})
                elif block.type == "image_url":
                    dicts.append({
                        "type": "image_url",
                        "image_url": {"url": block.url, "detail": block.detail},
                    })
        return json.dumps(dicts, ensure_ascii=False)

    @staticmethod
    def _deserialize_content(raw: str) -> str | list[dict]:
        """从存储字符串恢复 content。

        纯文本 → 原样返回
        JSON 数组 → list[dict]（OpenAI API 格式）
        """
        if not raw.startswith("["):
            return raw
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "type" in parsed[0]:
                return parsed
        except (json.JSONDecodeError, IndexError, KeyError):
            pass
        return raw

    # ── Message CRUD ──

    def save_message(self, session_id: str, role: str, content: str | list,
                     tool_calls: list | None = None):
        """保存一条消息到会话。"""
        now = time.time()
        tool_calls_json = json.dumps(tool_calls or [], ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, self._serialize_content(content), tool_calls_json, now),
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
                    (session_id, msg["role"],
                     self._serialize_content(msg.get("content", "")),
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
            msg = {"role": r["role"], "content": self._deserialize_content(r["content"])}
            tcs = json.loads(r["tool_calls"])
            if tcs:
                msg["tool_calls"] = tcs
            history.append(msg)
        return history

    # ── Search ──

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """全文搜索消息内容，返回匹配的消息及其会话信息。

        优先使用 FTS5，无结果时降级到 LIKE（处理中文等 unicode61 无法正确分词的语言）。
        """
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

        if not rows:
            # FTS5 未命中时回退 LIKE（兼容 CJK 等非 ASCII 文本）
            like_q = f"%{query}%"
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT m.session_id, s.title AS session_title, "
                    "m.role, m.content, m.created_at "
                    "FROM messages m "
                    "JOIN sessions s ON m.session_id = s.id "
                    "WHERE m.content LIKE ? "
                    "ORDER BY m.created_at DESC LIMIT ?",
                    (like_q, limit),
                ).fetchall()

        return [dict(r) for r in rows]

    # ── Tool Call Log ──

    def insert_tool_call(self, session_id: str, turn_number: int,
                         tool_name: str, status: str = "success",
                         duration_ms: float = 0.0,
                         error_message: str = ""):
        """记录一次工具调用。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO tool_call_log (session_id, turn_number, tool_name, "
                "status, duration_ms, error_message, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, turn_number, tool_name, status,
                 duration_ms, error_message, time.time()),
            )

    def get_tool_call_stats(self, session_id: str | None = None,
                            days: int | None = None) -> list[dict]:
        """返回工具调用聚合统计，可选按会话或天数过滤。"""
        where_clauses = []
        params: list = []
        if session_id:
            where_clauses.append("t.session_id = ?")
            params.append(session_id)
        if days is not None:
            where_clauses.append("t.created_at >= ?")
            params.append(time.time() - days * 86400)

        where = ""
        if where_clauses:
            where = "WHERE " + " AND ".join(where_clauses)

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT t.tool_name, "
                f"COUNT(*) AS call_count, "
                f"SUM(CASE WHEN t.status = 'error' THEN 1 ELSE 0 END) AS error_count, "
                f"AVG(t.duration_ms) AS avg_duration_ms "
                f"FROM tool_call_log t {where} "
                f"GROUP BY t.tool_name ORDER BY call_count DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Usage Log ──

    def insert_usage(self, session_id: str, model: str,
                     prompt_tokens: int, completion_tokens: int,
                     latency_ms: int, cost_estimate: float = 0.0,
                     cache_read_tokens: int = 0):
        """记录一次 LLM 调用用量。"""
        # 旧库可能没有 cache_read_tokens 列，用 ALTER TABLE 兼容
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    "ALTER TABLE usage_log ADD COLUMN cache_read_tokens "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            except Exception:
                pass  # 列已存在
            conn.execute(
                "INSERT INTO usage_log (session_id, model, prompt_tokens, "
                "completion_tokens, cache_read_tokens, latency_ms, "
                "cost_estimate, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, model, prompt_tokens, completion_tokens,
                 cache_read_tokens, latency_ms, cost_estimate, time.time()),
            )

    def get_session_usage(self, session_id: str) -> list[dict]:
        """获取指定会话的所有用量记录。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM usage_log WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session_stats(self, session_id: str) -> dict:
        """获取指定会话的聚合统计。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS call_count, "
                "COALESCE(SUM(prompt_tokens), 0) AS total_prompt, "
                "COALESCE(SUM(completion_tokens), 0) AS total_completion, "
                "COALESCE(SUM(cost_estimate), 0) AS total_cost "
                "FROM usage_log WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else {
            "call_count": 0, "total_prompt": 0,
            "total_completion": 0, "total_cost": 0.0,
        }

    def summary_stats(self) -> dict[str, int]:
        """返回全库聚合统计（跨所有会话）。"""
        with self._connect() as conn:
            sessions = conn.execute("SELECT COUNT(*) AS cnt FROM sessions").fetchone()
            messages = conn.execute("SELECT COUNT(*) AS cnt FROM messages").fetchone()
            usage = conn.execute(
                "SELECT COALESCE(SUM(prompt_tokens), 0) AS prompt, "
                "COALESCE(SUM(completion_tokens), 0) AS completion, "
                "COALESCE(SUM(cost_estimate), 0) AS cost "
                "FROM usage_log"
            ).fetchone()
        return {
            "total_sessions": sessions["cnt"] if sessions else 0,
            "total_messages": messages["cnt"] if messages else 0,
            "total_prompt_tokens": usage["prompt"] if usage else 0,
            "total_completion_tokens": usage["completion"] if usage else 0,
            "total_estimated_cost": round(usage["cost"], 6) if usage else 0.0,
        }

    # ── Insights 聚合查询 ──

    def cost_by_model(self, days: int = 7) -> list[dict]:
        """按模型汇总指定天数内的费用和调用量。"""
        cutoff = time.time() - days * 86400
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT model, COUNT(*) AS call_count, "
                "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
                "COALESCE(SUM(cost_estimate), 0) AS total_cost "
                "FROM usage_log WHERE created_at >= ? "
                "GROUP BY model ORDER BY total_cost DESC",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def daily_cost_trend(self, days: int = 30) -> list[dict]:
        """返回每日费用趋势。"""
        cutoff = time.time() - days * 86400
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DATE(created_at, 'unixepoch') AS day, "
                "COUNT(*) AS call_count, "
                "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
                "COALESCE(SUM(cost_estimate), 0) AS total_cost "
                "FROM usage_log WHERE created_at >= ? "
                "GROUP BY day ORDER BY day",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def session_portrait(self, session_id: str) -> dict:
        """返回单个会话的完整画像。"""
        with self._connect() as conn:
            # 会话基本信息
            sess = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if sess is None:
                return {}
            sess_dict = dict(sess)

            # 消息数
            msg_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            msg_count = msg_row["cnt"] if msg_row else 0

            # LLM 用量
            usage = conn.execute(
                "SELECT COUNT(*) AS call_count, "
                "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
                "COALESCE(SUM(cost_estimate), 0) AS total_cost, "
                "COALESCE(AVG(latency_ms), 0) AS avg_latency_ms "
                "FROM usage_log WHERE session_id = ?",
                (session_id,),
            ).fetchone()

            # 工具调用
            tools = conn.execute(
                "SELECT tool_name, COUNT(*) AS call_count, "
                "SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_count, "
                "AVG(duration_ms) AS avg_duration_ms "
                "FROM tool_call_log WHERE session_id = ? "
                "GROUP BY tool_name ORDER BY call_count DESC",
                (session_id,),
            ).fetchall()

        return {
            "session_id": session_id,
            "title": sess_dict.get("title", ""),
            "created_at": sess_dict.get("created_at", 0),
            "updated_at": sess_dict.get("updated_at", 0),
            "msg_count": msg_count,
            "llm_calls": dict(usage) if usage else {},
            "tool_calls": [dict(t) for t in tools],
        }

    # ── Helpers ──

    # ── 子 Agent 持久化 ──

    def save_sub_agent(self, record: dict) -> None:
        """写入或更新一条子 Agent 记录。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sub_agents
                   (id, session_id, parent_id, agent_name, status, task,
                    output, error, prompt_tokens, completion_tokens,
                    tool_calls, started_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record.get("session_id", ""),
                    record.get("parent_id"),
                    record["agent_name"],
                    record["status"],
                    record["task"],
                    record.get("output", ""),
                    record.get("error", ""),
                    record.get("prompt_tokens", 0),
                    record.get("completion_tokens", 0),
                    json.dumps(record.get("tool_calls", [])),
                    record["started_at"],
                    record.get("completed_at"),
                ),
            )

    def get_session_sub_agents(self, session_id: str) -> list[dict]:
        """查询某会话的所有子 Agent 记录。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sub_agents WHERE session_id = ? ORDER BY started_at",
                (session_id,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tool_calls"] = json.loads(d.get("tool_calls") or "[]")
            result.append(d)
        return result

    def save_sub_agent_event(
        self,
        sub_agent_id: str,
        from_status: str,
        to_status: str,
        *,
        metadata: str = "{}",
    ) -> None:
        """记录子 Agent 状态变更事件。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sub_agent_events (sub_agent_id, from_status, to_status, timestamp, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (sub_agent_id, from_status, to_status, time.time(), metadata),
            )

    @staticmethod
    def _generate_id(now: float) -> str:
        import random
        ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        suffix = f"{random.randrange(1000, 9999)}"  # noqa: S311
        return f"{ts}-{suffix}"
