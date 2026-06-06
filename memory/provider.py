"""MemoryProvider — 可插拔记忆提供者抽象基类

MemoryProvider 是外部记忆系统接入 chips-agent 的统一契约。
所有提供者通过 MemoryManager 注册，与内置记忆（MEMORY.md/USER.md）共存。

关键生命周期：
  initialize()            — 初始化连接/资源
  system_prompt_block()   — 注入 system prompt 的内容
  prefetch(query)         — 每轮对话前检索相关上下文
  sync_turn(user, asst)   — 每轮对话后持久化
  get_tool_schemas()      — 暴露给模型的工具 schema
  handle_tool_call()      — 工具调用路由
  shutdown()              — 清理

内置提供者始终存在，仅允许一个外部提供者同时注册。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MemoryProvider(ABC):
    """可插拔记忆提供者。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """唯一标识符，如 'builtin', 'holographic'。"""

    @abstractmethod
    def is_available(self) -> bool:
        """检查配置/凭据/依赖是否就绪。"""

    def initialize(self, session_id: str = "", **kwargs) -> None:
        """初始化（连接数据库、创建资源等）。"""

    def system_prompt_block(self) -> str:
        """返回注入 system prompt 的静态文本，空字符串表示不注入。"""
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """根据当前查询检索相关记忆上下文，返回格式化文本。"""
        return ""

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "") -> None:
        """持久化一轮完成的对话。"""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """为下一轮对话发起后台预检索。"""

    @abstractmethod
    def get_tool_schemas(self) -> list[dict]:
        """返回此提供者暴露的工具 schema 列表（OpenAI function-calling 格式）。"""

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        """处理工具调用。仅对 get_tool_schemas() 返回的工具名调用。"""
        raise NotImplementedError(f"{self.name} 未处理工具 {tool_name}")

    def shutdown(self) -> None:
        """清理资源。"""

    # ── 可选生命周期钩子 ──

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """每轮开始时的通知。"""

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """会话结束时的通知。"""

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: dict | None = None) -> None:
        """内置记忆工具写入时的通知（用于镜像）。"""

    def on_session_switch(self, new_session_id: str, *,
                          parent_session_id: str = "", reset: bool = False) -> None:
        """会话切换时的通知。"""
