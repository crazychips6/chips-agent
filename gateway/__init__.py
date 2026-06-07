"""Gateway — LLM 多模型网关

分层结构：
  gateway/__init__.py      — 导出
  gateway/types.py          — ChatResult
  gateway/protocol.py       — ModelGateway ABC
  gateway/providers/        — provider 实现
  gateway/rate_limit.py     — 令牌桶限流
"""

from .protocol import ModelGateway
from .types import ChatResult

__all__ = ["ChatResult", "ModelGateway"]
