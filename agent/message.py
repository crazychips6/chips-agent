"""ContentBlock 类型体系与 OpenAI 序列化。

Phase 12 A1：为多模态消息提供类型抽象。
不依赖项目内部任何模块。"""

import json
import os
import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TextBlock:
    """纯文本内容块。"""
    type: Literal["text"] = "text"
    text: str = ""


@dataclass(frozen=True)
class ImageBlock:
    """图片内容块（image_url）。"""
    type: Literal["image_url"] = "image_url"
    url: str = ""
    detail: Literal["auto", "low", "high"] = "auto"


ContentBlock = TextBlock | ImageBlock


def to_openai_content(content: str | list) -> str | list[dict]:
    """将内部 content 转换为 OpenAI API 接受的格式。

    - str → 原样返回
    - list[ContentBlock] → [{ "type": "...", ... }, ...]
    - list[dict]（已序列化格式）→ 原样返回
    """
    if isinstance(content, str):
        return content
    result: list[dict] = []
    for block in content:
        if isinstance(block, TextBlock):
            result.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageBlock):
            result.append({
                "type": "image_url",
                "image_url": {"url": block.url, "detail": block.detail},
            })
        elif isinstance(block, dict):
            result.append(block)  # 已序列化格式（恢复等场景）
    return result


def contains_image_block(content: str | list) -> bool:
    """检查已序列化的 content 是否包含图片块。"""
    if isinstance(content, str):
        return False
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and block.get("type") == "image_url"
            for block in content
        )
    return False


def image_file_to_data_uri(path: str) -> str:
    """读取图片文件，返回 data URI。"""
    import base64
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(path)[1].lower()
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp",
    }.get(ext.lstrip("."), "image/png")
    return f"data:{mime};base64,{b64}"


def to_openai_messages(messages: list[dict]) -> list[dict]:
    """将内部消息列表（dict 格式）转换为 OpenAI API 接受的格式。

    处理 ContentBlock 序列化，兼容纯文本消息。
    """
    api_messages: list[dict] = []
    for msg in messages:
        d: dict = {"role": msg["role"]}
        content = msg.get("content", "")
        d["content"] = to_openai_content(content)
        if "tool_calls" in msg:
            d["tool_calls"] = msg["tool_calls"]
        if "tool_call_id" in msg:
            d["tool_call_id"] = msg["tool_call_id"]
        api_messages.append(d)
    return api_messages


# ── 用户输入解析 ──

_IMAGE_URL_RE = re.compile(
    r'((?:https?://)[^\s()<>"\']+\.(?:png|jpg|jpeg|gif|webp)(?:\?[^\s()<>"\']*)?)',
    re.IGNORECASE,
)
_IMAGE_PATH_RE = re.compile(
    r'((?:/[^\s]+|\.\.?/[^\s]+)\.(?:png|jpg|jpeg|gif|webp))',
    re.IGNORECASE,
)


def parse_user_content(text: str) -> str | list[ContentBlock | dict]:
    """解析用户输入，检测图片 URL/文件路径并转为 ContentBlock。

    纯文本输入 → 原样返回 str
    含图片引用 → 返回 list[ContentBlock | dict]
    """
    # 第一阶段：检测 URL 图片
    parts = _IMAGE_URL_RE.split(text)
    if len(parts) > 1:
        blocks: list[ContentBlock | dict] = []
        for i, part in enumerate(parts):
            if i % 2:  # 匹配到的 URL
                blocks.append(ImageBlock(url=part))
            elif part:
                blocks.append(TextBlock(text=part))
        return blocks if blocks else text

    # 第二阶段：检测本地文件路径
    parts = _IMAGE_PATH_RE.split(text)
    if len(parts) > 1:
        blocks = []
        for i, part in enumerate(parts):
            if i % 2:  # 匹配到的路径
                if os.path.isfile(part):
                    try:
                        data_uri = image_file_to_data_uri(part)
                        blocks.append(ImageBlock(url=data_uri))
                        continue
                    except Exception:
                        pass
                blocks.append(TextBlock(text=part))
            elif part:
                blocks.append(TextBlock(text=part))
        return blocks if blocks else text

    return text


# ── 存储序列化 ──


def serialize_content(content: str | list) -> str:
    """将 content 序列化为可存储的 JSON 字符串。

    - str → 原样返回
    - list[ContentBlock] → JSON 序列化
    """
    if isinstance(content, str):
        return content
    return json.dumps(to_openai_content(content), ensure_ascii=False)


def deserialize_content(raw: str) -> str | list[dict]:
    """从存储字符串恢复 content。

    - 纯文本 → 原样返回
    - JSON 格式的 ContentBlock → list[dict]
    """
    if not raw.startswith("["):
        return raw
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "type" in parsed[0]:
            return parsed
    except (json.JSONDecodeError, IndexError, KeyError):
        pass
    return raw
