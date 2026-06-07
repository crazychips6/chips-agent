"""Gateway 数据类型 — ChatResult 统一返回结构"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatResult:
    """统一的 LLM 调用返回。

    tool_calls 格式（OpenAI function-calling 兼容）::

        [{"id": "call_xxx", "type": "function",
          "function": {"name": "...", "arguments": '{"arg":"val"}'}}]
    """
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None
    usage: dict[str, int] | None = None  # {"prompt_tokens": N, "completion_tokens": N}
    model: str = ""
    latency_ms: int = 0
