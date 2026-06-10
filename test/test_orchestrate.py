"""Test orchestrate — 多 Agent 编排

mock gateway + registry 避免真实调用。
"""
import json
from unittest.mock import MagicMock

import pytest

from agent.loop import AIAgent
from gateway.types import ChatResult


@pytest.fixture(autouse=True)
def reset_globals():
    """确保每测前全局状态清空。"""
    import tool.builtins.agent_tools as at
    at._parent = None
    at._registry = None
    from agent.pool import reset as reset_pool
    reset_pool()


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.chat.return_value = ChatResult(content="子任务已完成")
    return gw


@pytest.fixture
def mock_registry():
    """模拟 AgentRegistry，所有已知角色都可用。"""
    def _get(name):
        known = {
            "researcher": {
                "model": "deepseek-chat", "tools": ["web"],
                "max_iterations": 20, "system_prompt": "研究助手",
                "pool_size": 3,
            },
            "coder": {
                "model": "deepseek-chat", "tools": ["terminal", "file"],
                "max_iterations": 15, "system_prompt": "编程助手",
            },
            "reviewer": {
                "model": "deepseek-chat", "tools": ["file"],
                "max_iterations": 10, "system_prompt": "审查助手",
            },
        }
        return known.get(name)

    reg = MagicMock()
    reg.get.side_effect = _get
    reg.names = ["researcher", "coder", "reviewer"]
    return reg


def _make_parent(mock_gateway) -> AIAgent:
    from tool.registry import registry
    agent = AIAgent(model="deepseek-chat", gateway=mock_gateway)
    agent.registry = registry
    from tool.toolsets import resolve_multiple_toolsets
    agent.enabled_toolsets = ["core"]
    agent.tool_names = set(resolve_multiple_toolsets(["core"])) & registry.tool_names
    return agent


def _setup(mock_gateway, mock_registry):
    """wire parent + registry，返回 orchestrator handler。"""
    from tool.builtins.agent_tools import wire_parent, wire_registry
    from tool.builtins.orchestrate_tool import _handle as orch
    parent = _make_parent(mock_gateway)
    wire_parent(parent)
    wire_registry(mock_registry)
    return orch


class TestOrchestrateSupervisor:
    def test_basic_serial(self, mock_gateway, mock_registry):
        orch = _setup(mock_gateway, mock_registry)
        result = json.loads(orch({
            "mode": "supervisor", "goal": "测试",
            "steps": [
                {"agent": "researcher", "task": "搜索A"},
                {"agent": "coder", "task": "写代码"},
            ],
        }))
        assert result["mode"] == "supervisor"
        assert result["total_steps"] == 2
        assert len(result["results"]) == 2
        assert result["results"][0]["agent"] == "researcher"
        assert result["results"][1]["agent"] == "coder"

    def test_parallel(self, mock_gateway, mock_registry):
        orch = _setup(mock_gateway, mock_registry)
        mock_gateway.chat.return_value = ChatResult(content="并行任务完成")
        result = json.loads(orch({
            "mode": "supervisor",
            "steps": [
                {"agent": "researcher", "task": "搜索A"},
                {"agent": "researcher", "task": "搜索B"},
                {"agent": "coder", "task": "写代码"},
            ],
            "parallel": True,
        }))
        assert len(result["results"]) == 3
        for r in result["results"]:
            assert "output" in r or "error" in r

    def test_empty_steps(self, mock_gateway, mock_registry):
        orch = _setup(mock_gateway, mock_registry)
        result = json.loads(orch({"mode": "supervisor", "steps": []}))
        assert "error" in result

    def test_not_wired(self):
        from tool.builtins.orchestrate_tool import _handle as orch
        result = json.loads(orch({"mode": "supervisor", "steps": [{"agent": "r", "task": "t"}]}))
        assert "error" in result


class TestOrchestratePipeline:
    def test_two_stages(self, mock_gateway, mock_registry):
        orch = _setup(mock_gateway, mock_registry)
        mock_gateway.chat.return_value = ChatResult(content="阶段输出")
        result = json.loads(orch({
            "mode": "pipeline",
            "steps": [
                {"agent": "coder", "task": "写代码"},
                {"agent": "reviewer", "task": "审查代码"},
            ],
        }))
        assert result["mode"] == "pipeline"
        assert len(result["results"]) == 2

    def test_single_stage(self, mock_gateway, mock_registry):
        orch = _setup(mock_gateway, mock_registry)
        result = json.loads(orch({
            "mode": "pipeline",
            "steps": [{"agent": "coder", "task": "写代码"}],
        }))
        assert len(result["results"]) == 1

    def test_empty_steps(self, mock_gateway, mock_registry):
        orch = _setup(mock_gateway, mock_registry)
        result = json.loads(orch({"mode": "pipeline", "steps": []}))
        assert "error" in result


class TestOrchestrateDebate:
    def test_basic_debate(self, mock_gateway, mock_registry):
        orch = _setup(mock_gateway, mock_registry)
        result = json.loads(orch({
            "mode": "debate",
            "agents": ["researcher", "coder"],
            "task": "这个方案怎么样？",
        }))
        assert result["mode"] == "debate"
        assert len(result["results"]) == 2

    def test_empty_agents(self, mock_gateway, mock_registry):
        orch = _setup(mock_gateway, mock_registry)
        result = json.loads(orch({"mode": "debate", "agents": [], "task": "test"}))
        assert "error" in result

    def test_empty_task(self, mock_gateway, mock_registry):
        orch = _setup(mock_gateway, mock_registry)
        result = json.loads(orch({"mode": "debate", "agents": ["r"], "task": ""}))
        assert "error" in result


class TestOrchestrateUnknownMode:
    def test_unknown_mode(self, mock_gateway, mock_registry):
        orch = _setup(mock_gateway, mock_registry)
        result = json.loads(orch({"mode": "unknown"}))
        assert "error" in result
