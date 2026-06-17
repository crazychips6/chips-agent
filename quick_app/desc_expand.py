"""描述展开 — 把一句话描述展开成结构化信息供检索和模板使用

用户说"帮我做个天气工具"
  → LLM 展开成：
    name: weather_forecast
    summary: 查询指定城市的天气预报
    input_spec: [{name: city, type: string, template: str}]
    output_spec: [{name: result, type: string, template: str}]
    keywords: [weather, forecast, 天气, 预报]
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from quick_app.models import ExpandedDescription, TemplateType

logger = logging.getLogger("chips.quick_app.desc_expand")

# 模板映射：Python 类型 → TemplateType
TYPE_TO_TEMPLATE = {
    "string": TemplateType.STR,
    "str": TemplateType.STR,
    "integer": TemplateType.STR,
    "int": TemplateType.STR,
    "float": TemplateType.STR,
    "number": TemplateType.STR,
    "file": TemplateType.FILE_SELECT,
    "image": TemplateType.FILE_SELECT,
    "path": TemplateType.FILE_SELECT,
    "url": TemplateType.LINK,
}

EXPAND_PROMPT = """你是一个快应用（QuickApp）需求分析器。用户用一句话描述他想要的功能，你需要把它展开成结构化的检索信息。

返回 JSON 格式，不要多余的文字：
{{
  "name": "英文snake_case工具名",
  "summary": "一句话概述（20字内）",
  "description": "详细功能描述（50字内）",
  "input_spec": [
    {{"name": "参数名", "type": "string|integer|file|image|url", "template": "str|file_select|link", "description": "参数说明"}}
  ],
  "output_spec": [
    {{"name": "输出名", "type": "string|file", "template": "str|file_output", "description": "输出说明"}}
  ],
  "keywords": ["搜索关键词", "至少3个", "中英文混合"]
}}

模板类型说明：
- str — 纯文本输入/输出框
- file_select — 从本地目录选文件
- link — URL 链接输入
- file_output — 文件路径输出（写入本地目录）

用户描述：{description}"""


def expand_description(
    description: str,
    llm_call: Callable[[str], str] | None = None,
) -> ExpandedDescription:
    """把一句话描述展开成结构化信息。

    先尝试调 LLM 展开，失败时用规则兜底。

    Args:
        description: 用户的一句话描述。
        llm_call: LLM 调用函数（接收 prompt 返回文本），None 时只用规则。

    Returns:
        展开后的结构化描述。
    """
    if llm_call:
        try:
            prompt = EXPAND_PROMPT.format(description=description)
            raw = llm_call(prompt)
            return _parse_llm_response(raw, description)
        except Exception as e:
            logger.warning("desc_expand llm failed: %s", e)

    return _rule_based_fallback(description)


def _parse_llm_response(raw: str, original: str) -> ExpandedDescription:
    """解析 LLM 返回的 JSON。"""
    raw = raw.strip()
    # 提取 JSON（可能被 markdown 代码块包裹）
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    data = json.loads(raw)

    return ExpandedDescription(
        name=data.get("name", _infer_name(original)),
        summary=data.get("summary", original[:30]),
        description=data.get("description", original),
        input_spec=_validate_spec(data.get("input_spec", [])),
        output_spec=_validate_spec(data.get("output_spec", [])),
        keywords=data.get("keywords", []),
    )


def _validate_spec(spec: list[dict]) -> list[dict]:
    """确保 spec 字段完整。"""
    validated = []
    for item in spec:
        tpl = item.get("template", "str")
        if tpl not in ("str", "file_select", "link", "file_output", "file_upload"):
            tpl = TYPE_TO_TEMPLATE.get(item.get("type", ""), TemplateType.STR).value
        validated.append({
            "name": item.get("name", "unknown"),
            "type": item.get("type", "string"),
            "template": tpl,
            "description": item.get("description", ""),
        })
    return validated


def _rule_based_fallback(description: str) -> ExpandedDescription:
    """LLM 不可用时用规则生成。"""
    name = _infer_name(description)
    return ExpandedDescription(
        name=name,
        summary=description[:30],
        description=description,
        input_spec=[],
        output_spec=[{"name": "result", "type": "string", "template": "str", "description": "执行结果"}],
        keywords=[description[:10]],
    )


def _infer_name(description: str) -> str:
    """从描述推断工具名。"""
    import re
    # 取前两个有意义的词
    words = re.findall(r"[a-zA-Z一-鿿]+", description)
    if not words:
        return "my_tool"
    # 去掉"工具"、"制作"等通用词
    skip = {"工具", "制作", "帮我", "做一个"}
    filtered = [w for w in words if w.lower() not in skip]
    if not filtered:
        return "my_tool"
    name = "_".join(filtered[:3]).lower()
    # 转拼音？不转，直接用中文或英文
    return name
