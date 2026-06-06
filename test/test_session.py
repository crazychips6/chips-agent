"""session/db 模块测试

全覆盖 SessionDB 的 CRUD 操作。使用临时文件避免污染持久化数据。"""

import os
import tempfile

import pytest

from session.db import SessionDB


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    _db = SessionDB(path)
    yield _db
    _db._lock.acquire()  # ensure clean close
    _db._lock.release()
    os.unlink(path)


class TestCreateSession:
    def test_create_returns_id(self, db):
        sid = db.create_session()
        assert isinstance(sid, str)
        assert len(sid) > 10

    def test_create_with_title(self, db):
        sid = db.create_session(title="test")
        sess = db.get_session(sid)
        assert sess["title"] == "test"

    def test_create_unique_ids(self, db):
        s1 = db.create_session()
        s2 = db.create_session()
        assert s1 != s2

    def test_get_session_nonexistent(self, db):
        assert db.get_session("not-exist") is None


class TestSaveAndGetHistory:
    def test_save_and_read(self, db):
        sid = db.create_session()
        db.save_message(sid, "user", "hello")
        db.save_message(sid, "assistant", "hi there")
        history = db.get_history(sid)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "hello"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "hi there"

    def test_empty_history(self, db):
        sid = db.create_session()
        assert db.get_history(sid) == []

    def test_tool_calls_preserved(self, db):
        sid = db.create_session()
        tcs = [{"id": "call_1", "type": "function", "function": {"name": "echo", "arguments": '{"text":"hi"}'}}]
        db.save_message(sid, "assistant", "", tool_calls=tcs)
        history = db.get_history(sid)
        assert len(history) == 1
        assert history[0]["tool_calls"] == tcs

    def test_save_messages_batch(self, db):
        sid = db.create_session()
        msgs = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "reply1"},
            {"role": "user", "content": "msg2"},
        ]
        db.save_messages(sid, msgs)
        history = db.get_history(sid)
        assert len(history) == 3

    def test_multiple_sessions_isolated(self, db):
        s1 = db.create_session()
        s2 = db.create_session()
        db.save_message(s1, "user", "only in s1")
        assert len(db.get_history(s1)) == 1
        assert len(db.get_history(s2)) == 0


class TestListSessions:
    def test_list_empty(self, db):
        assert db.list_sessions() == []

    def test_list_ordered(self, db):
        import time
        s1 = db.create_session(title="first")
        time.sleep(0.01)
        s2 = db.create_session(title="second")
        sessions = db.list_sessions()
        assert sessions[0]["id"] == s2
        assert sessions[1]["id"] == s1

    def test_list_msg_count(self, db):
        sid = db.create_session()
        db.save_message(sid, "user", "a")
        db.save_message(sid, "assistant", "b")
        sessions = db.list_sessions()
        assert sessions[0]["msg_count"] == 2


class TestSearch:
    def test_search_finds_content(self, db):
        sid = db.create_session()
        db.save_message(sid, "user", "我的 API key 是 sk-test123")
        db.save_message(sid, "assistant", "已保存密钥")
        results = db.search("API")
        assert len(results) >= 1
        assert results[0]["role"] == "user"

    def test_search_no_match(self, db):
        sid = db.create_session()
        db.save_message(sid, "user", "hello world")
        assert db.search("xyz") == []

    def test_search_across_sessions(self, db):
        s1 = db.create_session(title="chat1")
        s2 = db.create_session(title="chat2")
        db.save_message(s1, "user", "讨论 Python")
        db.save_message(s2, "user", "讨论 Rust")
        results = db.search("Python")
        assert len(results) == 1
        assert results[0]["session_title"] == "chat1"


class TestDelete:
    def test_delete_session_removes_messages(self, db):
        sid = db.create_session()
        db.save_message(sid, "user", "data")
        db.delete_session(sid)
        assert db.get_session(sid) is None
        assert db.get_history(sid) == []


class TestEdgeCases:
    def test_large_content(self, db):
        sid = db.create_session()
        large = "x" * 100_000
        db.save_message(sid, "user", large)
        history = db.get_history(sid)
        assert len(history[0]["content"]) == 100_000

    def test_unicode_content(self, db):
        sid = db.create_session()
        db.save_message(sid, "user", "你好世界 🎉")
        history = db.get_history(sid)
        assert history[0]["content"] == "你好世界 🎉"

    def test_recreate_same_db(self, db):
        """相同的数据库文件重新打开后数据仍在。"""
        sid = db.create_session()
        db.save_message(sid, "user", "persist me")
        path = db._db_path

        db2 = SessionDB(path)
        history = db2.get_history(sid)
        assert len(history) == 1
        assert history[0]["content"] == "persist me"

    def test_save_content_block_list(self, db):
        """ContentBlock 列表自动序列化为 JSON 存储。"""
        sid = db.create_session()
        content = [{"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {"url": "https://img.png"}}]
        db.save_message(sid, "user", content)
        history = db.get_history(sid)
        assert isinstance(history[0]["content"], list)
        assert history[0]["content"][0]["type"] == "text"
        assert history[0]["content"][1]["type"] == "image_url"

    def test_save_mixed_content_in_batch(self, db):
        """批量保存中包含 ContentBlock 和纯文本。"""
        sid = db.create_session()
        db.save_messages(sid, [
            {"role": "user", "content": "纯文本"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://img.png"}}]},
        ])
        history = db.get_history(sid)
        assert len(history) == 2
        assert history[0]["content"] == "纯文本"
        assert isinstance(history[1]["content"], list)

    def test_content_roundtrip_preserves_data(self, db):
        """ContentBlock 存/读往返后数据一致。"""
        sid = db.create_session()
        original = [{"type": "text", "text": "描述"}, {"type": "image_url", "image_url": {"url": "https://img.png", "detail": "high"}}]
        db.save_message(sid, "user", original)
        history = db.get_history(sid)
        assert history[0]["content"] == original

    def test_plain_text_unaffected(self, db):
        """纯文本存/读不受序列化影响。"""
        sid = db.create_session()
        db.save_message(sid, "user", "hello")
        db.save_message(sid, "assistant", "world")
        history = db.get_history(sid)
        assert history[0]["content"] == "hello"
        assert history[1]["content"] == "world"

    def test_save_content_block_dataclass_objects(self, db):
        """ContentBlock dataclass 对象（非 dict）也能正确序列化。"""
        from agent.message import TextBlock, ImageBlock
        sid = db.create_session()
        content = [TextBlock(text="看图"), ImageBlock(url="https://img.png")]
        db.save_message(sid, "user", content)
        history = db.get_history(sid)
        assert isinstance(history[0]["content"], list)
        assert history[0]["content"][0]["type"] == "text"
        assert history[0]["content"][1]["type"] == "image_url"
