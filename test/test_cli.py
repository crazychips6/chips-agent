"""CLI 集成测试 — 验证 agent 的完整组装链路

模拟 cli.py 的 wiring 流程，验证 registry → memory → context → prompt 的串联结果。
"""

from unittest.mock import MagicMock, patch

import pytest

from agent.loop import AIAgent
from memory.store import MemoryStore
from tool.registry import ToolRegistry
from tool.toolsets import resolve_toolset


@pytest.fixture
def clean_registry():
    """提供干净 registry，测试后清理注册的工具。"""
    r = ToolRegistry()
    r.register(name="echo", toolset="core", handler=lambda args: args.get("text", ""))
    r.register(name="memory_read", toolset="memory", handler=lambda _: "read ok")
    r.register(name="memory_write", toolset="memory", handler=lambda _: "write ok")
    return r


@pytest.fixture
def mock_openai():
    with patch("agent.loop.OpenAI") as mock:
        client = MagicMock()
        mock.return_value = client

        msg = MagicMock()
        msg.content = "回复"
        msg.reasoning_content = None
        msg.tool_calls = None
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=msg)]
        )
        yield client


class TestAgentWiring:
    """模拟 CLI 的 wiring 流程，验证 agent 状态和 prompt 结果。"""

    def test_full_wiring(self, clean_registry, mock_openai, tmp_path):
        """完整链路：registry + tools + memory + context → 7 层 prompt。"""
        agent = AIAgent(api_key="test-key", base_url="http://test", model="test")
        agent.registry = clean_registry

        # 注入 toolset
        agent.tool_names = resolve_toolset("core") & clean_registry.tool_names

        # 注入 memory
        mem_dir = str(tmp_path / ".memory")
        memory_store = MemoryStore(memory_dir=mem_dir)
        memory_store.add("项目记忆", category="memory")
        memory_store.add("用户喜欢 Python", category="user")
        agent.memory = memory_store
        import tool.builtins.memory as memory_tool
        memory_tool._store = memory_store

        # 注入 context 文件
        ctx_file = tmp_path / "CHIP.md"
        ctx_file.write_text("# 项目约定\n使用 type hints")
        agent.context_files = [(str(ctx_file), "CHIP.md", "# 项目约定\n使用 type hints")]

        # 执行一次对话
        agent.run_conversation("你好")

        # 验证 prompt 包含 7 层
        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        system = call_kwargs["messages"][0]["content"]

        layers = ["核心身份", "当前日期", "用户偏好", "持久记忆",
                   "项目上下文", "工具规则", "调用约定"]
        for layer in layers:
            assert f"# {layer}" in system, f"缺少层: {layer}"

    def test_wiring_no_memory(self, clean_registry, mock_openai):
        """--no-memory 场景：只有 core 工具集，无记忆层。"""
        agent = AIAgent(api_key="test-key", base_url="http://test", model="test")
        agent.registry = clean_registry

        # 只注入 core，不注入 memory
        agent.tool_names = resolve_toolset("core") & clean_registry.tool_names
        agent.memory = None
        agent.context_files = []

        agent.run_conversation("hi")

        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        system = call_kwargs["messages"][0]["content"]

        assert "# 核心身份" in system
        assert "# 当前日期" in system
        assert "# 工具规则" in system
        assert "# 调用约定" in system
        assert "# 用户偏好" not in system
        assert "# 持久记忆" not in system
        assert "# 项目上下文" not in system

    def test_context_files_flow_into_prompt(self, clean_registry, mock_openai, tmp_path):
        """上下文文件内容正确注入到 prompt 的项目上下文层。"""
        agent = AIAgent(api_key="test-key", base_url="http://test", model="test")
        agent.registry = clean_registry
        agent.tool_names = clean_registry.tool_names
        agent.memory = None

        ctx = tmp_path / "CONTEXT.md"
        ctx.write_text("测试项目的上下文数据")
        agent.context_files = [(str(ctx), "CONTEXT.md", "测试项目的上下文数据")]

        agent.run_conversation("做什么")

        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        system = call_kwargs["messages"][0]["content"]
        assert "测试项目的上下文数据" in system
        assert "CONTEXT.md" in system


class TestToolsetAndMemoryWiring:
    """验证 toolset 解析 + memory wiring 的组合逻辑。"""

    def test_tool_names_after_wiring(self, clean_registry):
        """注入 core + memory 后 tool_names 内容正确。"""
        agent = AIAgent(api_key="test-key", base_url="http://test", model="test")
        agent.registry = clean_registry

        # 模拟 cli.py 的 wiring 顺序
        agent.tool_names = resolve_toolset("core") & clean_registry.tool_names
        assert agent.tool_names == {"echo"}

        # 再追加 memory 工具集
        agent.tool_names |= resolve_toolset("memory") & clean_registry.tool_names
        assert agent.tool_names == {"echo", "memory_read", "memory_write"}

    def test_tool_names_core_only(self, clean_registry):
        """只加载 core 工具集。"""
        agent = AIAgent(api_key="test-key", base_url="http://test", model="test")
        agent.registry = clean_registry
        agent.tool_names = resolve_toolset("core") & clean_registry.tool_names
        assert agent.tool_names == {"echo"}
        assert "memory_read" not in agent.tool_names
