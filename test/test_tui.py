"""Test TUI module — 终端用户界面（基于 Textual）"""

from unittest.mock import MagicMock

import pytest

from agent.tui import ChipsApp
from agent.tui.widgets.tool_call import ToolCall, _json_preview
from agent.tui.widgets.status_bar import StatusBar
from agent.tui.widgets.startup_panel import StartupPanel
from agent.tui.widgets.user_message import UserMessage
from agent.tui.widgets.assistant_message import AssistantMessage
from agent.tui.widgets.message_list import MessageList
from agent.tui.messages import AgentChunk, ToolEvent, AgentDone
from agent.tui.worker import AgentWorker


class TestChipsApp:
    """ChipsApp 主应用"""

    def test_create_app(self):
        """创建 ChipsApp 实例。"""
        agent = MagicMock()
        agent.model = "test-model"
        agent.tool_names = set()
        agent.context_files = []
        agent.memory_manager = MagicMock()
        agent.memory_manager.providers = []
        agent.session_id = "test-session"

        from agent.repl import CommandRegistry
        cmd_reg = CommandRegistry()
        startup_info = {
            "model": "test-model",
            "tool_count": 5,
            "toolset_names": ["core"],
            "memory_status": "off",
            "mcp_status": "off",
            "skill_status": "off",
            "compress_status": "on",
            "context_file_count": 0,
        }

        app = ChipsApp(agent, cmd_reg, startup_info)
        assert app.agent == agent
        assert app.cmd_registry == cmd_reg
        assert app.startup_info == startup_info


class TestToolCall:
    """ToolCall 工具调用卡片"""

    def test_create_tool_call(self):
        """创建 ToolCall 实例。"""
        card = ToolCall(name="web_search", args={"query": "天气"})
        assert card._name == "web_search"
        assert card._args == {"query": "天气"}
        assert card.collapsed is True

    def test_set_result(self):
        """设置工具调用结果。"""
        card = ToolCall(name="web_search", args={"query": "天气"})
        card.set_result("上海今天晴，22-28°C")
        assert card._result == "上海今天晴，22-28°C"
        assert card.collapsed is False

    def test_json_preview(self):
        """测试 JSON 预览函数。"""
        preview = _json_preview({"query": "天气", "limit": 10})
        assert "query=天气" in preview
        assert "limit=10" in preview

    def test_json_preview_long_value(self):
        """测试长值的 JSON 预览。"""
        long_value = "x" * 50
        preview = _json_preview({"key": long_value})
        assert "..." in preview


class TestStatusBar:
    """StatusBar 状态栏"""

    def test_create_status_bar(self):
        """创建 StatusBar 实例。"""
        status = StatusBar()
        status.model = "deepseek-chat"
        status.tool_count = 10
        status.tokens = "1234 / 5678"
        status.session = "abc123"
        assert status.model == "deepseek-chat"

    def test_render(self):
        """测试渲染输出。"""
        status = StatusBar()
        status.model = "deepseek-chat"
        status.tool_count = 10
        rendered = status.render()
        assert "deepseek-chat" in rendered


class TestStartupPanel:
    """StartupPanel 启动面板"""

    def test_create_startup_panel(self):
        """创建 StartupPanel 实例。"""
        panel = StartupPanel(
            model="deepseek-chat",
            tool_count=10,
            toolset_names=["core", "dev"],
            memory_status="builtin",
            mcp_status="2 servers",
            skill_status="5 skills",
        )
        assert panel is not None

    def test_startup_panel_with_defaults(self):
        """使用默认参数创建 StartupPanel。"""
        panel = StartupPanel(
            model="test-model",
            tool_count=5,
            toolset_names=["core"],
        )
        assert panel is not None


class TestMessages:
    """Textual Messages"""

    def test_agent_chunk(self):
        """测试 AgentChunk 消息。"""
        msg = AgentChunk(text="hello")
        assert msg.text == "hello"

    def test_tool_event(self):
        """测试 ToolEvent 消息。"""
        msg = ToolEvent(name="web_search", args={"query": "天气"}, result="结果")
        assert msg.name == "web_search"
        assert msg.args == {"query": "天气"}
        assert msg.result == "结果"

    def test_agent_done(self):
        """测试 AgentDone 消息。"""
        msg = AgentDone(reply="完成")
        assert msg.reply == "完成"


class TestWorker:
    """AgentWorker"""

    def test_create_worker(self):
        """创建 AgentWorker 实例。"""
        agent = MagicMock()
        worker = AgentWorker(agent)
        assert worker.agent == agent
