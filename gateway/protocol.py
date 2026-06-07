"""ModelGateway — LLM 调用抽象基类

所有 LLM provider 通过此接口接入 chips-agent。
loop.py 只依赖此接口，不依赖具体 provider 实现。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from .types import ChatResult


class ModelGateway(ABC):
    """LLM 网关抽象。"""

    @abstractmethod
    def chat(self, messages: list[dict[str, Any]], model: str = "",
             **kwargs: Any) -> ChatResult:
        """非流式 LLM 调用。"""

    def chat_stream(self, messages: list[dict[str, Any]], model: str = "",
                    *, on_chunk: Callable[[str], None] | None = None,
                    **kwargs: Any) -> ChatResult:
        """流式 LLM 调用。默认回退为非流式。

        on_chunk 回调用于实时输出文本 delta（如逐字打印）。
        支持流式的 provider 应覆盖此方法以获得更好的用户体验。
        """
        return self.chat(messages, model, **kwargs)
