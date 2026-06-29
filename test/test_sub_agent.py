"""测试 SubAgentManager — 子 Agent 生命周期管理"""

import time
import pytest

from agent.sub_agent import AgentStatus, SubAgentManager


class TestAgentStatus:
    def test_enum_values(self):
        assert AgentStatus.CREATED.value == "created"
        assert AgentStatus.RUNNING.value == "running"
        assert AgentStatus.COMPLETED.value == "completed"
        assert AgentStatus.FAILED.value == "failed"
        assert AgentStatus.CANCELLED.value == "cancelled"

    def test_legal_transitions(self):
        # CREATED → RUNNING
        assert AgentStatus.CREATED.can_transition_to(AgentStatus.RUNNING)
        # RUNNING → COMPLETED / FAILED / CANCELLED
        assert AgentStatus.RUNNING.can_transition_to(AgentStatus.COMPLETED)
        assert AgentStatus.RUNNING.can_transition_to(AgentStatus.FAILED)
        assert AgentStatus.RUNNING.can_transition_to(AgentStatus.CANCELLED)

    def test_illegal_transitions(self):
        # 跳过状态
        assert not AgentStatus.CREATED.can_transition_to(AgentStatus.COMPLETED)
        assert not AgentStatus.CREATED.can_transition_to(AgentStatus.FAILED)
        # 终态不可变
        assert not AgentStatus.COMPLETED.can_transition_to(AgentStatus.RUNNING)
        assert not AgentStatus.FAILED.can_transition_to(AgentStatus.CREATED)
        assert not AgentStatus.CANCELLED.can_transition_to(AgentStatus.RUNNING)
        # 反向
        assert not AgentStatus.RUNNING.can_transition_to(AgentStatus.CREATED)

    def test_validate_legal_does_not_raise(self):
        AgentStatus.validate_transition(AgentStatus.CREATED, AgentStatus.RUNNING)

    def test_validate_illegal_raises(self):
        with pytest.raises(ValueError, match="非法状态转换"):
            AgentStatus.validate_transition(AgentStatus.CREATED, AgentStatus.COMPLETED)


class TestSubAgentManager:
    def test_create(self):
        mgr = SubAgentManager()
        rid = mgr.create("test_agent", "测试任务")
        assert rid is not None
        assert len(rid) == 12  # uuid hex[:12]

    def test_create_initial_status(self):
        mgr = SubAgentManager()
        rid = mgr.create("test_agent", "测试任务")
        record = mgr.get(rid)
        assert record is not None
        assert record.status == AgentStatus.CREATED
        assert record.agent_name == "test_agent"
        assert record.task == "测试任务"

    def test_update_status_legal(self):
        mgr = SubAgentManager()
        rid = mgr.create("test_agent", "测试任务")
        mgr.update(rid, status=AgentStatus.RUNNING)
        assert mgr.get(rid).status == AgentStatus.RUNNING
        mgr.update(rid, status=AgentStatus.COMPLETED)
        assert mgr.get(rid).status == AgentStatus.COMPLETED

    def test_update_status_string_auto_convert(self):
        mgr = SubAgentManager()
        rid = mgr.create("test_agent", "测试任务")
        mgr.update(rid, status="running")  # 字符串自动转枚举
        assert mgr.get(rid).status == AgentStatus.RUNNING

    def test_update_status_illegal_raises(self):
        mgr = SubAgentManager()
        rid = mgr.create("test_agent", "测试任务")
        with pytest.raises(ValueError, match="非法状态转换"):
            mgr.update(rid, status=AgentStatus.COMPLETED)  # CREATED → COMPLETED 非法

    def test_terminal_state_immutable(self):
        mgr = SubAgentManager()
        rid = mgr.create("test_agent", "测试任务")
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.update(rid, status=AgentStatus.COMPLETED)
        with pytest.raises(ValueError, match="非法状态转换"):
            mgr.update(rid, status=AgentStatus.RUNNING)  # COMPLETED → RUNNING 非法

    def test_update_other_fields(self):
        mgr = SubAgentManager()
        rid = mgr.create("test_agent", "测试任务")
        mgr.update(rid, output="完成", prompt_tokens=100)
        record = mgr.get(rid)
        assert record.output == "完成"
        assert record.prompt_tokens == 100

    def test_update_unknown_id_raises(self):
        mgr = SubAgentManager()
        with pytest.raises(ValueError, match="Unknown"):
            mgr.update("nonexistent", status=AgentStatus.RUNNING)

    def test_capture_result_success(self):
        mgr = SubAgentManager()
        rid = mgr.create("test_agent", "测试")
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.capture_result(rid, [], "成功输出")
        record = mgr.get(rid)
        assert record.status == AgentStatus.COMPLETED
        assert record.output == "成功输出"
        assert record.completed_at is not None

    def test_capture_result_error(self):
        mgr = SubAgentManager()
        rid = mgr.create("test_agent", "测试")
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.capture_result(rid, [], "", error="出错啦")
        record = mgr.get(rid)
        assert record.status == AgentStatus.FAILED
        assert record.error == "出错啦"

    def test_list_by_role(self):
        mgr = SubAgentManager()
        r1 = mgr.create("researcher", "搜1")
        mgr.update(r1, status=AgentStatus.RUNNING)
        mgr.update(r1, status=AgentStatus.COMPLETED)
        r2 = mgr.create("coder", "写1")
        results = mgr.list("researcher")
        assert len(results) == 1
        assert results[0]["agent_name"] == "researcher"

    def test_list_all(self):
        mgr = SubAgentManager()
        mgr.create("a", "t1")
        mgr.create("b", "t2")
        assert len(mgr.list_all()) == 2

    def test_to_list_item_converts_status(self):
        mgr = SubAgentManager()
        rid = mgr.create("test", "t")
        mgr.update(rid, status=AgentStatus.RUNNING)
        items = mgr.list("test")
        assert len(items) == 1
        assert items[0]["status"] == "running"  # 字符串，非枚举

    def test_get_sub_agent_result_status_string(self):
        mgr = SubAgentManager()
        rid = mgr.create("test", "t")
        mgr.update(rid, status=AgentStatus.RUNNING)
        record = mgr.get(rid)
        from dataclasses import asdict
        d = asdict(record)
        assert hasattr(d["status"], "value")  # asdict 保留枚举

    def test_max_history(self):
        mgr = SubAgentManager(max_history_per_role=3)
        ids = []
        for i in range(5):
            rid = mgr.create("test_agent", f"任务{i}")
            ids.append(rid)
        # 只保留最新的 3 条
        records = mgr.list("test_agent")
        assert len(records) == 3
        # 最旧的 2 条被淘汰
        assert mgr.get(ids[0]) is None
        assert mgr.get(ids[1]) is None
        # 最新的 3 条还在
        assert mgr.get(ids[2]) is not None
        assert mgr.get(ids[3]) is not None
        assert mgr.get(ids[4]) is not None


# ── 持久化测试（依赖 SessionDB） ──


class TestSubAgentPersistence:
    @pytest.fixture
    def session_db(self, tmp_path):
        from session.db import SessionDB
        db = SessionDB(str(tmp_path / "test.db"))
        return db

    def test_persist_on_create(self, session_db):
        mgr = SubAgentManager()
        session_id = session_db.create_session("test")
        mgr.set_session(session_db, session_id)
        rid = mgr.create("researcher", "搜索任务")
        rows = session_db.get_session_sub_agents(session_id)
        assert len(rows) == 1
        assert rows[0]["id"] == rid
        assert rows[0]["agent_name"] == "researcher"
        assert rows[0]["status"] == "created"

    def test_persist_on_update(self, session_db):
        mgr = SubAgentManager()
        session_id = session_db.create_session("test")
        mgr.set_session(session_db, session_id)
        rid = mgr.create("researcher", "搜索任务")
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.update(rid, status=AgentStatus.COMPLETED, output="完成")
        rows = session_db.get_session_sub_agents(session_id)
        assert len(rows) == 1
        assert rows[0]["status"] == "completed"
        assert rows[0]["output"] == "完成"

    def test_event_logged_on_transition(self, session_db):
        mgr = SubAgentManager()
        session_id = session_db.create_session("test")
        mgr.set_session(session_db, session_id)
        rid = mgr.create("researcher", "搜索任务")
        mgr.update(rid, status=AgentStatus.RUNNING)
        # 直接查 DB 确认事件表有记录
        with session_db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sub_agent_events WHERE sub_agent_id = ?", (rid,)
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["from_status"] == "created"
        assert rows[0]["to_status"] == "running"

    def test_restore_history(self, session_db):
        mgr = SubAgentManager()
        session_id = session_db.create_session("test")
        mgr.set_session(session_db, session_id)
        mgr.create("researcher", "搜1")
        rid = mgr.create("coder", "写1")
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.update(rid, status=AgentStatus.COMPLETED, output="代码完成")

        # 新建一个 Manager，从 DB 恢复
        mgr2 = SubAgentManager()
        mgr2.set_session(session_db, session_id)
        count = mgr2.restore(session_id)
        assert count == 2
        assert mgr2.get(rid) is not None
        record = mgr2.get(rid)
        assert record is not None
        assert record.status == AgentStatus.COMPLETED
        assert record.output == "代码完成"

    def test_restore_empty(self, session_db):
        mgr = SubAgentManager()
        session_id = session_db.create_session("test")
        mgr.set_session(session_db, session_id)
        count = mgr.restore(session_id)
        assert count == 0

    def test_capture_result_persists(self, session_db):
        mgr = SubAgentManager()
        session_id = session_db.create_session("test")
        mgr.set_session(session_db, session_id)
        rid = mgr.create("researcher", "搜索")
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.capture_result(rid, [], "搜索完成", error=None)
        rows = session_db.get_session_sub_agents(session_id)
        assert rows[0]["status"] == "completed"
        assert rows[0]["output"] == "搜索完成"


# ── 钩子测试 ──


class TestSubAgentHooks:
    def test_on_created_fired(self):
        mgr = SubAgentManager()
        fired = []
        mgr.on("created", lambda r: fired.append(r.id))
        rid = mgr.create("test", "t")
        assert len(fired) == 1
        assert fired[0] == rid

    def test_on_running_fired(self):
        mgr = SubAgentManager()
        fired = []
        mgr.on("running", lambda r: fired.append(r.id))
        rid = mgr.create("test", "t")
        mgr.update(rid, status=AgentStatus.RUNNING)
        assert len(fired) == 1
        assert fired[0] == rid

    def test_on_completed_fired(self):
        mgr = SubAgentManager()
        fired = []
        mgr.on("completed", lambda r: fired.append(r.id))
        rid = mgr.create("test", "t")
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.update(rid, status=AgentStatus.COMPLETED)
        assert len(fired) == 1

    def test_on_failed_fired(self):
        mgr = SubAgentManager()
        fired = []
        mgr.on("failed", lambda r: fired.append(r.id))
        rid = mgr.create("test", "t")
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.update(rid, status=AgentStatus.FAILED)
        assert len(fired) == 1

    def test_on_cancelled_fired(self):
        mgr = SubAgentManager()
        fired = []
        mgr.on("cancelled", lambda r: fired.append(r.id))
        rid = mgr.create("test", "t")
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.update(rid, status=AgentStatus.CANCELLED)
        assert len(fired) == 1

    def test_hook_exception_does_not_crash(self):
        mgr = SubAgentManager()

        def _boom(record):
            raise RuntimeError("boom")

        mgr.on("created", _boom)
        rid = mgr.create("test", "t")  # 不应该抛异常
        assert rid is not None

    def test_off_removes_hook(self):
        mgr = SubAgentManager()
        fired = []
        fn = lambda r: fired.append(r.id)
        mgr.on("created", fn)
        mgr.off("created", fn)
        mgr.create("test", "t")
        assert len(fired) == 0

    def test_multiple_hooks_same_event(self):
        mgr = SubAgentManager()
        results = []
        mgr.on("created", lambda r: results.append("a"))
        mgr.on("created", lambda r: results.append("b"))
        mgr.create("test", "t")
        assert results == ["a", "b"]


# ── TTL 测试 ──


class TestSubAgentTTL:
    def test_create_with_ttl_returns_id(self):
        mgr = SubAgentManager(cleanup_interval=1)
        rid = mgr.create_with_ttl("test", "t", ttl=300)
        assert rid is not None

    def test_ttl_stored(self):
        mgr = SubAgentManager()
        rid = mgr.create_with_ttl("test", "t", ttl=300)
        assert rid in mgr._ttl

    def test_no_ttl_for_normal_create(self):
        mgr = SubAgentManager()
        rid = mgr.create("test", "t")
        assert rid not in mgr._ttl

    def test_ttl_removed_on_completion(self):
        mgr = SubAgentManager()
        rid = mgr.create_with_ttl("test", "t", ttl=300)
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.update(rid, status=AgentStatus.COMPLETED)
        assert rid not in mgr._ttl

    def test_ttl_removed_on_fail(self):
        mgr = SubAgentManager()
        rid = mgr.create_with_ttl("test", "t", ttl=300)
        mgr.update(rid, status=AgentStatus.RUNNING)
        mgr.update(rid, status=AgentStatus.FAILED)
        assert rid not in mgr._ttl

    def test_timeout_auto_cancels(self):
        """TTL 过期后自动取消。"""
        mgr = SubAgentManager(cleanup_interval=1)
        rid = mgr.create_with_ttl("test", "t", ttl=0)  # 立即过期
        mgr.update(rid, status=AgentStatus.RUNNING)
        # 触发一次检查
        mgr._check_timeouts()
        record = mgr.get(rid)
        assert record is not None
        assert record.status == AgentStatus.CANCELLED, \
            f"expected CANCELLED, got {record.status}"

    def test_cleaner_start_stop(self):
        mgr = SubAgentManager()
        assert mgr._cleaner_thread is None
        mgr.start_cleaner()
        assert mgr._cleaner_thread is not None
        assert mgr._cleaner_thread.is_alive()
        mgr.stop_cleaner()
        assert mgr._cleaner_thread is None

    def test_double_start_cleaner(self):
        """重复启动不创建多个线程。"""
        mgr = SubAgentManager()
        mgr.start_cleaner()
        t1 = mgr._cleaner_thread
        mgr.start_cleaner()
        t2 = mgr._cleaner_thread
        assert t1 is t2  # 同一个线程
        mgr.stop_cleaner()
