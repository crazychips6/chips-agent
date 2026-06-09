"""CLI 集成测试 — 验证 agent 的完整组装链路

模拟 cli.py 的 wiring 流程，验证 registry → memory → context → prompt 的串联结果。
"""

from unittest.mock import MagicMock

import pytest

from agent.loop import AIAgent
from gateway.types import ChatResult
from memory.manager import MemoryManager
from memory.providers.builtin import BuiltinMemoryProvider
from tool.registry import registry as global_registry
from tool.toolsets import resolve_toolset


@pytest.fixture(autouse=True)
def _auto_clean_registry():
    """清理全局 registry 并注册 echo。"""
    from tool.registry import registry as r
    r.deregister("echo")
    r.register(name="echo", toolset="core", handler=lambda args: args.get("text", ""))
    yield r
    r.deregister("echo")


@pytest.fixture
def mock_gateway():
    """提供 mock gateway，避免实际 API 调用。"""
    gw = MagicMock()
    gw.chat.return_value = ChatResult(content="回复")
    return gw


class TestAgentWiring:
    """模拟 CLI 的 wiring 流程，验证 agent 状态和 prompt 结果。"""

    def test_full_wiring(self, mock_gateway, tmp_path):
        """完整链路：registry + tools + MemoryManager + context → 全部 prompt 层。"""
        agent = AIAgent(gateway=mock_gateway)
        agent.registry = global_registry

        # 注入 toolset
        agent.tool_names = set(resolve_toolset("core")) & global_registry.tool_names

        # 注入 MemoryManager + BuiltinMemoryProvider
        mem_dir = str(tmp_path / ".memory")
        mm = MemoryManager()
        mm.add_provider(BuiltinMemoryProvider(memory_dir=mem_dir))
        # 预写入记忆（通过 provider 的 handle_tool_call）
        mm.handle_tool_call("memory", {"action": "add", "target": "memory", "content": "项目记忆"})
        mm.handle_tool_call("memory", {"action": "add", "target": "user", "content": "用户喜欢 Python"})
        agent.memory_manager = mm

        # 注入 context 文件
        ctx_file = tmp_path / "CHIP.md"
        ctx_file.write_text("# 项目约定\n使用 type hints")
        agent.context_files = [(str(ctx_file), "CHIP.md", "# 项目约定\n使用 type hints")]

        # 执行一次对话
        agent.run_conversation("你好")

        # 验证 prompt 包含全部层
        call_kwargs = mock_gateway.chat.call_args[1]
        system = call_kwargs["messages"][0]["content"]

        layers = ["核心身份", "当前日期", "持久记忆",
                   "项目上下文", "调用约定"]
        for layer in layers:
            assert f"# {layer}" in system, f"缺少层: {layer}"

    def test_wiring_no_memory(self, mock_gateway):
        """--no-memory 场景：只有 core 工具集，无记忆层。"""
        agent = AIAgent(gateway=mock_gateway)
        agent.registry = global_registry

        # 只注入 core，不注入 memory
        agent.tool_names = set(resolve_toolset("core")) & global_registry.tool_names
        agent.context_files = []

        agent.run_conversation("hi")

        call_kwargs = mock_gateway.chat.call_args[1]
        system = call_kwargs["messages"][0]["content"]

        assert "# 核心身份" in system
        assert "# 当前日期" in system
        assert "# 调用约定" in system
        assert "# 工具规则" not in system
        assert "# 持久记忆" not in system
        assert "# 项目上下文" not in system

    def test_context_files_flow_into_prompt(self, mock_gateway, tmp_path):
        """上下文文件内容正确注入到 prompt 的项目上下文层。"""
        agent = AIAgent(gateway=mock_gateway)
        agent.registry = global_registry
        agent.tool_names = global_registry.tool_names

        ctx = tmp_path / "CONTEXT.md"
        ctx.write_text("测试项目的上下文数据")
        agent.context_files = [(str(ctx), "CONTEXT.md", "测试项目的上下文数据")]

        agent.run_conversation("做什么")

        call_kwargs = mock_gateway.chat.call_args[1]
        system = call_kwargs["messages"][0]["content"]
        assert "测试项目的上下文数据" in system
        assert "CONTEXT.md" in system


class TestToolsetAndMemoryWiring:
    """验证 toolset 解析 + memory wiring 的组合逻辑。"""

    def test_tool_names_after_wiring(self):
        """注入 core 后 tool_names 内容正确。"""
        agent = AIAgent(model="test")
        agent.registry = global_registry

        # 模拟 cli.py 的 wiring 顺序
        agent.tool_names = set(resolve_toolset("core")) & global_registry.tool_names
        assert agent.tool_names == {"echo"}

    def test_tool_names_core_only(self):
        """只加载 core 工具集。"""
        agent = AIAgent(model="test")
        agent.registry = global_registry
        agent.tool_names = set(resolve_toolset("core")) & global_registry.tool_names
        assert agent.tool_names == {"echo"}
