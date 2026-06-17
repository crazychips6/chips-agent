"""代码生成 — 根据 draft 生成 QuickApp handler 代码

核心职责：
  - 对已知工具（CLI / pip）使用 handler_pattern 模板填空
  - 对自写代码场景使用 LLM 生成
  - 确保输出符合 template 格式
"""

from __future__ import annotations

import json
import logging
from typing import Any

from quick_app.known_good import KNOWN_GOOD_INDEX
from quick_app.models import Draft, QUICK_APP_TEMPLATE, SourceType, build_schema

logger = logging.getLogger("chips.quick_app.codegen")


def generate_code(draft: Draft) -> str:
    """根据 draft 生成 app.py 完整代码。

    优先使用 known-good 索引中的 handler_pattern（参数填空），
    兜底使用 LLM 生成。

    Args:
        draft: 经用户确认的草案卡片。

    Returns:
        app.py 完整代码字符串。
    """
    # 1. 尝试在 known-good 索引中找精确匹配的条目
    entry = _find_matching_entry(draft)
    if entry and entry.handler_pattern:
        return _render_from_pattern(draft, entry)

    # 2. 没有 handler_pattern → 返回基本骨架等待 LLM 填充
    return _render_skeleton(draft)


def generate_code_with_llm(
    draft: Draft,
    llm_call: Any,
) -> str:
    """用 LLM 生成 handler 代码（兜底方案）。

    Args:
        draft: 草案卡片。
        llm_call: 可调用对象，接受 prompt 字符串，返回文本。

    Returns:
        app.py 完整代码字符串。
    """
    # 先看能否用 pattern 填空
    entry = _find_matching_entry(draft)
    if entry and entry.handler_pattern:
        return _render_from_pattern(draft, entry)

    # 没有 pattern → LLM 生成
    prompt = _build_code_gen_prompt(draft, entry)
    try:
        handler_body = llm_call(prompt)
    except Exception as e:
        logger.warning("codegen_llm_failed: %s", e)
        return _render_skeleton(draft)

    # 清理输出（LLM 可能返回 markdown 代码块）
    handler_body = _clean_llm_output(handler_body)

    return QUICK_APP_TEMPLATE.format(
        name=draft.name,
        description=draft.description,
        handler_body=_indent_body(handler_body),
    )


def _find_matching_entry(draft: Draft) -> Any | None:
    """在 known-good 索引中找与 draft source 匹配的条目。"""
    for entry in KNOWN_GOOD_INDEX:
        if entry.name.lower() == draft.source_name.lower():
            return entry
        # source_type 模糊匹配
        if entry.source_type == draft.source_type:
            # 检查关键词是否有交集
            desc_words = set(draft.description.lower().split())
            kw_words = set(k.lower() for k in entry.keywords)
            if desc_words & kw_words:
                return entry
    return None


def _render_from_pattern(draft: Draft, entry: Any) -> str:
    """从已知条目的 handler_pattern 生成代码。

    把 pattern 中的 {param_name} 占位符替换为 args["param_name"]。
    """
    # 替换占位符
    pattern = entry.handler_pattern
    for p in draft.params:
        pname = p["name"]
        placeholder = "{" + pname + "}"
        if placeholder in pattern:
            pattern = pattern.replace(placeholder, f'args["{pname}"]')

    # 生成完整的 handler 函数
    handler_body = _indent_body(pattern)

    return QUICK_APP_TEMPLATE.format(
        name=draft.name,
        description=draft.description,
        handler_body=handler_body,
    )


def _render_skeleton(draft: Draft) -> str:
    """返回仅包含骨架的代码（handler 返回占位消息）。"""
    params_desc = ", ".join(p["name"] for p in draft.params) or "无参数"
    body = f'# TODO: 实现 {draft.description}\n'
    body += f'# 参数：{params_desc}\n'
    body += f'# 来源：{draft.source_type.value} ({draft.source_name})\n'
    body += 'return f"已执行 {args}"'

    return QUICK_APP_TEMPLATE.format(
        name=draft.name,
        description=draft.description,
        handler_body=_indent_body(body),
    )


def _build_code_gen_prompt(draft: Draft, entry: Any | None) -> str:
    """构造 LLM 代码生成 prompt。"""
    params_json = json.dumps(draft.params, ensure_ascii=False, indent=2)
    lines = [
        f"请生成 QuickApp 的 handler 函数代码。",
        f"",
        f"# 工具信息",
        f"名称：{draft.name}",
        f"描述：{draft.description}",
        f"来源类型：{draft.source_type.value}",
        f"来源：{draft.source_name}",
        f"输出描述：{draft.output_description or '未指定'}",
        f"",
        f"# 参数定义",
        f"{params_json}",
    ]

    if entry:
        lines.extend([
            f"",
            f"# 参考信息（来自 known-good 索引）",
            f"已知工具：{entry.name}",
            f"安装提示：{entry.install_hint}",
            f"建议参数结构：{entry.fixed_params}",
        ])
        if entry.weak_scene_tags:
            lines.append(f"注意：该工具在以下场景可能效果不佳：{', '.join(entry.weak_scene_tags)}")

    if draft.source_type == SourceType.CLI_TOOL:
        lines.extend([
            f"",
            f"# 生成要求",
            f"使用 subprocess.run() 包装 CLI 命令",
            f"handler 接收 args: dict，返回 str（结果摘要或错误信息）",
            f"添加超时限制（60-120秒）",
            f"检查 returncode，非零时返回用户可读的错误",
            f"不要假设文件存在，添加合理的错误处理",
        ])
    elif draft.source_type == SourceType.PIP_PACKAGE:
        lines.extend([
            f"",
            f"# 生成要求",
            f"在函数内部 import 所需库（不要全局 import，避免子进程加载时未安装）",
            f"handler 接收 args: dict，返回 str",
            f"添加 try/except 错误处理",
        ])
    else:
        lines.extend([
            f"",
            f"# 生成要求",
            f"handler 接收 args: dict，返回 str",
            f"尽可能使用标准库，减少外部依赖",
            f"添加错误处理",
        ])

    lines.extend([
        f"",
        f"# 模板框架",
        f'def handler(args: dict) -> str:',
        f'    """{draft.description}"""',
        f"    # 请在此处编写 handler 代码",
        f"    pass",
        f"",
        f"只需返回 handler 函数代码（包含 def handler(...) 行），不要返回 __main__ 块。",
    ])

    return "\n".join(lines)


def _clean_llm_output(text: str) -> str:
    """清理 LLM 输出的代码。"""
    text = text.strip()
    # 移除 ```python 和 ``` 标记
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _indent_body(body: str, indent: int = 4) -> str:
    """给代码体每行添加缩进（默认 4 空格，和 docstring 同级）。"""
    lines = body.split("\n")
    indented = []
    for line in lines:
        if line.strip():
            indented.append(" " * indent + line)
        else:
            indented.append("")
    return "\n".join(indented)
