"""审计日志 — 安全的只追加事件日志，使用哈希链防篡改。

使用独立 SQLite 数据库 ~/.chips/audit.db。
对外提供 log_event() 便利函数和 AuditLog 类供直接使用。
不依赖项目内其他模块。"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

_AUDIT_DB_PATH = Path(os.environ.get("CHIPS_AUDIT_DB_PATH") or Path.home() / ".chips" / "audit.db")

_log_lock = threading.Lock()
_AUDIT_INSTANCE: "AuditLog | None" = None


class AuditLog:
    """审计日志存储。

    仅支持 log()（INSERT）和 query()（SELECT），不提供 UPDATE/DELETE。
    每条记录包含 prev_hash 指向上一条，形成哈希链，篡改可检测。
    线程安全。
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = os.path.abspath(db_path or str(_AUDIT_DB_PATH))
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
                CREATE TABLE IF NOT EXISTS audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type  TEXT NOT NULL,
                    event_data  TEXT NOT NULL,
                    prev_hash   TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL
                )
            """)

    def _last_entry(self) -> dict | None:
        """获取最后一条记录的完整数据。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, event_type, event_data, prev_hash, created_at"
                " FROM audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def compute_hash(entry: dict) -> str:
        raw = f"{entry['id']}|{entry['event_type']}|{entry['event_data']}|{entry['prev_hash']}|{entry['created_at']}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def log(self, event_type: str, event_data: dict):
        """写入一条审计日志。"""
        now = time.time()
        last = self._last_entry()
        prev_hash = self.compute_hash(last) if last else ""
        data_json = json.dumps(event_data, ensure_ascii=False, sort_keys=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (event_type, event_data, prev_hash, created_at) VALUES (?, ?, ?, ?)",
                (event_type, data_json, prev_hash, now),
            )

    def query(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """查询审计日志，最新在前。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, event_type, event_data, prev_hash, created_at"
                " FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def verify(self) -> list[dict]:
        """校验哈希链完整性，返回断点列表（空列表 = 完整）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, event_type, event_data, prev_hash, created_at"
                " FROM audit_log ORDER BY id"
            ).fetchall()
        broken = []
        prev_hash = ""
        for r in rows:
            entry = dict(r)
            if entry["prev_hash"] != prev_hash:
                broken.append({
                    "id": entry["id"],
                    "expected_prev": prev_hash,
                    "actual_prev": entry["prev_hash"],
                })
            prev_hash = self.compute_hash(entry)
        return broken

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM audit_log").fetchone()
        return row["cnt"] if row else 0


def _get_audit() -> AuditLog:
    """获取默认的 AuditLog 单例。"""
    global _AUDIT_INSTANCE  # noqa: PLW0603
    if _AUDIT_INSTANCE is None:
        _AUDIT_INSTANCE = AuditLog()
    return _AUDIT_INSTANCE


def log_event(event_type: str, event_data: dict):
    """便利函数：写入一条审计日志（使用默认单例）。"""
    _get_audit().log(event_type, event_data)
