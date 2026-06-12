"""Test TUI module — 终端用户界面"""

from unittest.mock import MagicMock

import pytest

from agent.tui import TUI


class TestTUIStartup:
    """启动信息面板"""

    def test_startup_plain(self):
        """纯文本模式（无 rich）下的启动信息。"""
        tui = TUI()
        # 强制走纯文本路径
        tui._rich = False
        # 不抛异常就算过
        tui.startup(
            model="test-model",
            tool_count=10,
            toolset_names=["core"],
            memory_status="builtin",
            mcp_status="off",
            skill_status="off",
            compress_status="on",
            context_file_count=0,
        )

    def test_startup_empty_toolsets(self):
        """无工具集时的启动。"""
        tui = TUI()
        tui._rich = False
        tui.startup(
            model="test-model",
            tool_count=0,
            toolset_names=[],
            memory_status="off",
        )

    def test_startup_rich_mode(self):
        """启动面板统一用 ANSI 盒子风格（不抛异常即可）。"""
        tui = TUI()
        tui.startup(
            model="test-model",
            tool_count=10,
            toolset_names=["core", "web"],
            memory_status="builtin",
            mcp_status="2 servers",
            skill_status="3 skills",
            compress_status="on",
            context_file_count=1,
        )


class TestTUIChat:
    """对话交互"""

    def test_show_user_plain(self):
        """纯文本模式展示用户消息。"""
        tui = TUI()
        tui._rich = False
        # 不抛异常
        tui._show_user("你好")

    def test_show_user_rich(self):
        """rich 模式下展示用户消息（使用 ANSI _cprint）。"""
        tui = TUI()
        # 不抛异常就算过
        tui._show_user("你好")

    def test_chat_non_streaming(self):
        """非流式模式（--no-stream）下的对话。"""
        agent = MagicMock()
        agent.run_conversation.return_value = "你好，有什么可以帮助？"

        tui = TUI()
        tui._rich = False
        reply = tui.chat(agent, "你好")
        assert reply == "你好，有什么可以帮助？"
        agent.run_conversation.assert_called_once()

    def test_chat_streaming_mode(self):
        """流式模式：chunk_callback 累积内容，返回 buffer。"""
        agent = MagicMock()

        def _run(text, chunk_callback=None, **kw):
            chunk_callback("hello ")
            chunk_callback("world")
            return ""
        agent.run_conversation.side_effect = _run

        tui = TUI()
        tui._rich = False
        reply = tui.chat(agent, "hi")
        assert reply == "hello world"

    def test_chat_with_tool_calls(self):
        """带工具调用的流式对话。"""
        agent = MagicMock()

        def _run(text, chunk_callback=None, **kw):
            for c in ["我来查一下", "上海天气", "晴转多云"]:
                chunk_callback(c)
            return ""
        agent.run_conversation.side_effect = _run

        tui = TUI()
        tui._rich = False
        reply = tui.chat(agent, "上海天气")
        assert "我来查一下" in reply
        assert "上海天气" in reply
        assert "晴转多云" in reply

    def test_chat_empty_response(self):
        """空响应不应崩溃。"""
        agent = MagicMock()
        agent.run_conversation.return_value = ""

        tui = TUI()
        tui._rich = False
        reply = tui.chat(agent, "你好")
        assert reply == ""
