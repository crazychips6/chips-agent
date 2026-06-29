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
