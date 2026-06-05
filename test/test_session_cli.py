"""chips session CLI 子命令测试"""

from pathlib import Path

from session.cli import handle_session
from session.db import SessionDB


class TestSessionCli:
    """所有测试使用临时 SQLite 文件，避免与真实 .chips/sessions.db 冲突。"""

    def _db(self, tmp_path: Path) -> SessionDB:
        return SessionDB(db_path=str(tmp_path / "test.db"))

    # ── helpers ──

    def _create_session(self, db: SessionDB, title="") -> str:
        return db.create_session(title=title)

    def _add_message(self, db: SessionDB, session_id: str, role: str, content: str):
        db.save_message(session_id, role, content)

    # ── list ──

    def test_list_empty(self, tmp_path, capsys, monkeypatch):
        db = self._db(tmp_path)
        monkeypatch.setattr("session.cli.SessionDB", lambda db_path: db)
        handle_session(_args("list"))
        out = capsys.readouterr().out
        assert "(无会话)" in out

    def test_list_with_sessions(self, tmp_path, capsys, monkeypatch):
        db = self._db(tmp_path)
        monkeypatch.setattr("session.cli.SessionDB", lambda db_path: db)
        sid = self._create_session(db, "test session")
        self._add_message(db, sid, "user", "hello")
        handle_session(_args("list"))
        out = capsys.readouterr().out
        assert sid in out
        assert "test session" in out
        assert "[1条]" in out

    # ── show ──

    def test_show_existing(self, tmp_path, capsys, monkeypatch):
        db = self._db(tmp_path)
        monkeypatch.setattr("session.cli.SessionDB", lambda db_path: db)
        sid = self._create_session(db, "my chat")
        self._add_message(db, sid, "user", "你好")
        self._add_message(db, sid, "assistant", "你好！有什么可以帮你？")
        handle_session(_args("show", session_id=sid))
        out = capsys.readouterr().out
        assert sid in out
        assert "my chat" in out
        assert "用户" in out
        assert "助手" in out

    def test_show_nonexistent(self, tmp_path, monkeypatch):
        db = self._db(tmp_path)
        monkeypatch.setattr("session.cli.SessionDB", lambda db_path: db)
        try:
            handle_session(_args("show", session_id="nonexistent"))
        except SystemExit:
            pass
        else:
            raise AssertionError("expected SystemExit")

    # ── search ──

    def test_search_found(self, tmp_path, capsys, monkeypatch):
        db = self._db(tmp_path)
        monkeypatch.setattr("session.cli.SessionDB", lambda db_path: db)
        sid = self._create_session(db, "test")
        self._add_message(db, sid, "user", "什么是量子计算")
        handle_session(_args("search", query="量子"))
        out = capsys.readouterr().out
        assert "量子计算" in out

    def test_search_not_found(self, tmp_path, capsys, monkeypatch):
        db = self._db(tmp_path)
        monkeypatch.setattr("session.cli.SessionDB", lambda db_path: db)
        sid = self._create_session(db, "test")
        self._add_message(db, sid, "user", "hello")
        handle_session(_args("search", query="nonexistent"))
        out = capsys.readouterr().out
        assert "未找到" in out

    # ── delete ──

    def test_delete_existing(self, tmp_path, capsys, monkeypatch):
        db = self._db(tmp_path)
        monkeypatch.setattr("session.cli.SessionDB", lambda db_path: db)
        sid = self._create_session(db, "to delete")
        handle_session(_args("delete", session_id=sid))
        out = capsys.readouterr().out
        assert "已删除" in out
        assert db.get_session(sid) is None

    def test_delete_nonexistent(self, tmp_path, monkeypatch):
        db = self._db(tmp_path)
        monkeypatch.setattr("session.cli.SessionDB", lambda db_path: db)
        try:
            handle_session(_args("delete", session_id="nonexistent"))
        except SystemExit:
            pass
        else:
            raise AssertionError("expected SystemExit")


def _args(action: str, **kwargs):
    from types import SimpleNamespace
    base = {"session_action": action}
    base.update(kwargs)
    return SimpleNamespace(**base)
