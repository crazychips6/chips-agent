"""Accurate token counting using tiktoken.

Usage::

    from agent.token_counter import count_tokens, count_messages_tokens

    tokens = count_tokens("hello world", model="deepseek-chat")
    total = count_messages_tokens(messages, model="gpt-4o")
"""

from __future__ import annotations

import logging

import tiktoken
from tiktoken import Encoding

logger = logging.getLogger("chips.agent.token_counter")

# Model → encoding 映射（非 OpenAI 模型需要手动指定）
_MODEL_ENCODING_OVERRIDE: dict[str, str] = {
    "deepseek-chat": "cl100k_base",
    "deepseek-reasoner": "cl100k_base",
}

_DEFAULT_ENCODING = "cl100k_base"


# 已知的 per-message 开销（来自 OpenAI API 文档）
_PER_MESSAGE_OVERHEAD = 3  # <|start|> + role + <|end|>
_PER_NAME_OVERHEAD = 1     # 如果 message 有 name 字段
_PER_TOOL_CALL_ID_OVERHEAD = 3


def _resolve_encoding(model: str) -> str:
    """返回 model 对应的 tiktoken encoding 名。"""
    if model in _MODEL_ENCODING_OVERRIDE:
        return _MODEL_ENCODING_OVERRIDE[model]
    try:
        return tiktoken.encoding_name_for_model(model)
    except KeyError:
        return _DEFAULT_ENCODING


def _get_encoding(model: str) -> Encoding | None:
    name = _resolve_encoding(model)
    try:
        return tiktoken.get_encoding(name)
    except Exception:
        logger.warning("Failed to get encoding '%s' for model '%s'", name, model)
        return None


def set_encoding_for_model(model: str, encoding: str) -> None:
    """手动指定 model 使用的 encoding（如 tiktoken 内置映射不包含该模型时使用）。"""
    _MODEL_ENCODING_OVERRIDE[model] = encoding


def count_tokens(text: str, model: str = "") -> int:
    """精确统计一段文本的 token 数。

    指定 model 时会尝试使用模型对应的 encoding，否则使用默认 cl100k_base 估算。
    当 encoding 不可用时回退到 len//4 粗略估算。
    """
    if not text:
        return 0
    enc = _get_encoding(model)
    if enc is None:
        return len(text) // 4
    return len(enc.encode(text))


def count_message_tokens(message: dict, model: str = "") -> int:
    """统计单条消息的 token 数，包含 role/content/tool_calls 开销。"""
    tokens = _PER_MESSAGE_OVERHEAD

    # role
    tokens += count_tokens(message.get("role", ""), model)

    # content（支持 str 和 ContentBlock list）
    content = message.get("content") or ""
    if isinstance(content, str):
        tokens += count_tokens(content, model)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                tokens += count_tokens(block.get("text", ""), model)
            elif block.get("type") == "image_url":
                # low_detail=85, high_detail=85+170*max(h,w)/512
                # 这里统一使用 low_detail 估算
                tokens += 85
            elif block.get("type") == "input_audio":
                tokens += 25  # 按 1 秒 audio 估算
            else:
                tokens += count_tokens(str(block), model)

    # name（可选）
    if "name" in message:
        tokens += _PER_NAME_OVERHEAD
        tokens += count_tokens(message["name"], model)

    # tool_calls
    for tc in message.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        tokens += _PER_TOOL_CALL_ID_OVERHEAD
        fn = tc.get("function", {})
        if isinstance(fn, dict):
            tokens += count_tokens(fn.get("name", ""), model)
            tokens += count_tokens(fn.get("arguments", ""), model)

    return tokens


def count_messages_tokens(messages: list[dict], model: str = "") -> int:
    """统计多条消息的总 token 数。"""
    return sum(count_message_tokens(m, model) for m in messages)
