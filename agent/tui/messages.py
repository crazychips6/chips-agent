"""Textual Message 定义 — 组件间通信。"""

from __future__ import annotations

from dataclasses import dataclass
from textual.message import Message


class AgentChunk(Message):
    """agent 流式输出一个文本片段。"""
    def __init__(self, text: str) -> None:
        self.text = text


class ToolEvent(Message):
    """agent 工具调用事件。"""
    def __init__(self, name: str, args: dict, result: str | None) -> None:
        self.name = name
        self.args = args
        self.result = result


class AgentDone(Message):
    """agent 回复完成。"""
    def __init__(self, reply: str) -> None:
        self.reply = reply
