"""ContextEngine — 上下文管理抽象基类

所有上下文引擎通过此接口接入 chips-agent。
loop.py 只依赖此接口，不依赖具体实现（如 Hermes ContextEngine 设计）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ContextEngine(ABC):
    """上下文管理引擎。控制对话上下文在接近 token 上限时的处理方式。"""

    # -- Token 状态（loop.py 读取用于日志） --

    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    threshold_tokens: int = 0
    compression_count: int = 0

    # -- 子类接口 --

    @property
    @abstractmethod
    def name(self) -> str:
        """引擎标识（如 'null', 'compressor'）。"""

    @abstractmethod
    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        """返回 True 时表示上下文需要压缩。"""

    @abstractmethod
    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """压缩消息列表，返回新列表。"""

    def update_from_response(self, usage: dict[str, int]) -> None:
        """每次 LLM 调用后记录用量。"""
        self.last_prompt_tokens = usage.get("prompt_tokens", 0) or 0
        self.last_completion_tokens = usage.get("completion_tokens", 0) or 0

    def on_session_reset(self) -> None:
        """/new 或 /reset 时重置状态。"""
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.compression_count = 0


class NullContextEngine(ContextEngine):
    """空引擎 — 永不压缩。"""

    @property
    def name(self) -> str:
        return "null"

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        return False

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages
