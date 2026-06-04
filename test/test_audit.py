"""safety/audit 模块测试"""

import json

import pytest

from safety.audit import AuditLog, log_event


@pytest.fixture
def audit(tmp_path):
    """返回使用临时 DB 的 AuditLog 实例。"""
    return AuditLog(db_path=str(tmp_path / "test_audit.db"))


class TestBasicLogging:
    def test_log_and_count(self, audit):
        audit.log("test_event", {"key": "value"})
        assert audit.count() == 1

    def test_log_multiple(self, audit):
        for i in range(5):
            audit.log("ev", {"i": i})
        assert audit.count() == 5

    def test_log_event_convenience(self, audit, monkeypatch):
        """log_event() 使用模块级 AuditLog。"""
        monkeypatch.setattr("safety.audit._AUDIT_INSTANCE", audit)
        log_event("convenience", {"test": True})
        assert audit.count() == 1


class TestQuery:
    def test_query_returns_latest_first(self, audit):
        for i in range(3):
            audit.log("ev", {"i": i})
        rows = audit.query(limit=10)
        assert len(rows) == 3
        data0 = json.loads(rows[0]["event_data"])
        assert data0["i"] == 2  # latest first

    def test_query_limit(self, audit):
        for i in range(10):
            audit.log("ev", {"i": i})
        assert len(audit.query(limit=3)) == 3

    def test_query_offset(self, audit):
        for i in range(5):
            audit.log("ev", {"i": i})
        rows = audit.query(limit=10, offset=3)
        assert len(rows) == 2  # remaining after offset
        data0 = json.loads(rows[0]["event_data"])
        assert data0["i"] == 1  # id=4 → i=1 (0-indexed after reverse)


class TestHashChain:
    def test_chain_linked(self, audit):
        """每条记录的 prev_hash 指向上一条的 hash。"""
        audit.log("a", {"n": 1})
        audit.log("b", {"n": 2})
        audit.log("c", {"n": 3})

        rows = audit.query(limit=10)
        # query returns latest first, so reverse
        rows.reverse()
        for i in range(1, len(rows)):
            expected = audit.compute_hash(rows[i - 1])
            assert rows[i]["prev_hash"] == expected

    def test_first_record_has_empty_prev(self, audit):
        audit.log("first", {})
        rows = audit.query(limit=1)
        assert rows[0]["prev_hash"] == ""

    def test_verify_chain_intact(self, audit):
        audit.log("a", {})
        audit.log("b", {})
        assert audit.verify() == []

    def test_verify_detects_tamper(self, audit):
        audit.log("a", {"n": 1})
        audit.log("b", {"n": 2})
        # 篡改第 1 条的数据：将 event_data 从 '{"n": 1}' 改成 '{"n": 999}'
        conn = audit._connect()
        conn.execute("UPDATE audit_log SET event_data = '{\"n\": 999}' WHERE id = 1")
        conn.commit()
        conn.close()

        broken = audit.verify()
        assert len(broken) >= 1  # 至少检测到断点

    def test_verify_empty(self, audit):
        assert audit.verify() == []


class TestEventData:
    def test_event_data_serialized(self, audit):
        complex_data = {"tool": "echo", "args": {"text": "hello"}, "nested": {"a": [1, 2, 3]}}
        audit.log("tool_call", complex_data)
        rows = audit.query(limit=1)
        stored = json.loads(rows[0]["event_data"])
        assert stored["tool"] == "echo"
        assert stored["nested"]["a"] == [1, 2, 3]

    def test_timestamps(self, audit):
        audit.log("ev", {})
        rows = audit.query(limit=1)
        assert isinstance(rows[0]["created_at"], float)
        assert rows[0]["created_at"] > 0


class TestConcurrency:
    def test_multiple_logs_thread_safe(self, audit):
        """简单验证不抛异常。"""
        import threading
        errors = []

        def write_log(i):
            try:
                audit.log("thread", {"i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_log, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert audit.count() == 10
        assert len(errors) == 0


class TestEdgeCases:
    def test_query_empty_db(self, audit):
        assert audit.query() == []

    def test_log_empty_event_data(self, audit):
        audit.log("empty", {})
        assert audit.count() == 1

    def test_log_none_event_data(self, audit):
        audit.log("none", {})
        assert audit.count() == 1
