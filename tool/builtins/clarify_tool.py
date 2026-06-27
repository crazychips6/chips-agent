"""clarify 工具 — 向用户追问澄清

当用户请求模糊或有多个可行方案时，LLM 用此工具向用户提出结构化问题。
支持多选（最多 4 项）和开放两种模式。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from tool.registry import registry

MAX_CHOICES = 4

# 模块级回调，由 cli.py 通过 wire_callback() 注入（例如 web 模式用 SSE 交互）
# 默认 None → 退化到 input()
_callback: Callable[[str, list[str] | None], str] | None = None


def wire_callback(cb: Callable[[str, list[str] | None], str]) -> None:
    global _callback
    _callback = cb


def _handle(args: dict[str, Any]) -> str:
    question = args.get("question", "").strip()
    if not question:
        return json.dumps({"error": "问题不能为空"})

    choices = args.get("choices")
    if choices is not None:
        if not isinstance(choices, list):
            return json.dumps({"error": "choices 必须是字符串列表"})
        choices = [str(c).strip() for c in choices if str(c).strip()]
        if len(choices) > MAX_CHOICES:
            choices = choices[:MAX_CHOICES]
        if not choices:
            choices = None

    cb = _callback
    if cb is not None:
        try:
            user_response = cb(question, choices)
        except Exception as e:
            return json.dumps({"error": f"获取用户输入失败: {e}"})
    else:
        # 退化到 stdin 输入
        try:
            text = question
            if choices:
                for i, c in enumerate(choices, 1):
                    text += f"\n  [{i}] {c}"
                text += f"\n  [{len(choices) + 1}] 其他"
            text += "\n> "
            user_response = input(text)
        except Exception as e:
            return json.dumps({"error": f"获取用户输入失败: {e}"})

    return json.dumps({
        "question": question,
        "choices_offered": choices,
        "user_response": str(user_response).strip(),
    }, ensure_ascii=False)


CLARIFY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "clarify",
        "description": "向用户追问澄清",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "问题",
                },
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": MAX_CHOICES,
                    "description": "选项列表（最多 4 项）",
                },
            },
            "required": ["question"],
        },
    },
}

registry.register(
    name="clarify",
    toolset="clarify",
    schema=CLARIFY_SCHEMA,
    handler=_handle,
)
