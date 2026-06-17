"""QuickApp 内置工具 — get_quick_app_draft / create_app / list_apps / delete_app

所有工具使用模块级 wiring 获取依赖（parent_agent / manager / registry）。
不阻塞主 Agent Chat（工具 handler 同步执行，调用方等待结果）。

约束：
  - get_quick_app_draft 是固定检索流水线（known-good 优先），不是 LLM 自由发挥
  - create_app 内部做双重校验：再次检查 known-good 索引中是否有匹配方案
  - toolset="quick_apps" 在 Manager 层写死，工具层无权修改
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.loop import AIAgent

from tool.registry import registry

from quick_app.codegen import generate_code, generate_code_with_llm
from quick_app.known_good import KNOWN_GOOD_INDEX
from quick_app.manager import QuickAppManager, get_manager
from quick_app.models import (
    Draft,
    SourceType,
    VenvLevel,
    build_schema,
)

logger = logging.getLogger("chips.tool.quick_app_tools")

# ── 模块级 wiring ──

_parent_agent: AIAgent | None = None  # type: ignore[assignment]


def wire_agent(agent: AIAgent) -> None:
    """注入主 Agent 引用（用于 create_app 的 LLM 代码生成）。"""
    global _parent_agent
    _parent_agent = agent


def get_tool_manager() -> QuickAppManager:
    """获取共享的 QuickAppManager 实例。"""
    return get_manager()


# ═══════════════════════════════════════════════════════════════
# get_quick_app_draft
# ═══════════════════════════════════════════════════════════════


def _match_keywords(description: str, keywords: list[str]) -> int:
    """返回 description 匹配关键词的数量。"""
    desc_lower = description.lower()
    count = 0
    for kw in keywords:
        if kw.lower() in desc_lower:
            count += 1
    return count


def _search_keywords(search_desc: str) -> list[dict]:
    """用展开后的关键词搜索 known-good 索引。"""
    # 取搜索用的关键词（展开描述中的 words）
    import re
    # 用更宽泛的匹配：拿 description 中的所有词
    words = re.findall(r"[a-zA-Z一-鿿]+", search_desc)
    combined = " ".join(words)
    return _search_known_good(combined)


def _search_known_good(description: str) -> list[dict]:
    """在 known-good 索引中搜索匹配条目。

    返回按匹配度排序的候选列表（降序）。
    """
    scored = []
    for entry in KNOWN_GOOD_INDEX:
        score = _match_keywords(description, entry.keywords)
        if score > 0:
            scored.append((score, entry))

    # 按匹配关键词数量降序
    scored.sort(key=lambda x: -x[0])

    results = []
    for score, entry in scored:
        # 参数模板：使用 entry 的 params_template 或从 fixed_params 推断
        params = list(entry.params_template) if entry.params_template else []
        if not params and entry.fixed_params:
            # 从 fixed_params 提取参数名
            for fp in entry.fixed_params:
                if fp.startswith("{") and fp.endswith("}"):
                    pname = fp[1:-1]
                    params.append({"name": pname, "type": "string", "description": pname})

        results.append({
            "name": _infer_name(entry.name, description),
            "description": entry.description,
            "source_type": entry.source_type.value,
            "source_name": entry.name,
            "source_reason": f"known-good 工具，场景匹配度 {score}",
            "params": params,
            "install_hint": entry.install_hint,
            "output_description": "",
            "venv_level": QuickAppManager.infer_venv_level(entry.source_type).value,
        })

    return results


def _infer_name(tool_name: str, description: str) -> str:
    """从描述推断工具名。

    优先使用工具原名，如果太长则从描述生成简短名。
    """
    # 先用工具名做基础，GitHub 的 user/repo 只取 repo 名
    base = tool_name.lower().replace("-", "_").replace(" ", "_")
    if "/" in base:
        base = base.split("/")[-1]  # GitHub: andkret/cookbook → cookbook

    # 如果工具名太通用（如 "requests"），从描述补充
    if len(base) < 3:
        # 取描述前两个词
        words = re.findall(r"[a-zA-Z一-鿿]+", description)
        if words:
            base = "_".join(words[:2]).lower()

    return base


def _handle_draft(args: dict[str, Any]) -> str:
    """get_quick_app_draft 处理器。

    返回的 draft 包含 `_trace` 字段记录每一步搜索详情（开发者调试用）。
    """
    description = args.get("description", "").strip()
    if not description:
        return json.dumps({"error": "缺少 description 参数"})

    from quick_app.trace_log import write as trace_write
    trace_write("DRAFT", f"开始检索: {description}", {"input": description})

    trace = {"steps": [], "original": description}

    # 0. 展开描述
    expanded = None
    input_templates = []
    output_templates = []
    try:
        from quick_app.desc_expand import expand_description
        parent = _parent_agent
        if parent and hasattr(parent, "gateway"):
            def _llm_call(prompt: str) -> str:
                resp = parent.gateway.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=parent.model, max_tokens=1500,
                )
                return resp.content or ""
            expanded = expand_description(description, llm_call=_llm_call)
            trace_write("DRAFT", "LLM 展开", {
                "name": expanded.name,
                "summary": expanded.summary,
                "input_spec": expanded.input_spec,
                "output_spec": expanded.output_spec,
                "keywords": expanded.keywords,
            })
            trace["steps"].append({
                "source": "llm_expand",
                "status": "ok",
                "name": expanded.name,
                "summary": expanded.summary,
                "keywords": expanded.keywords,
            })
            for spec in (expanded.input_spec or []):
                input_templates.append({
                    "param": spec.get("name", ""),
                    "template_type": spec.get("template", "str"),
                    "label": spec.get("description", ""),
                })
            for spec in (expanded.output_spec or []):
                output_templates.append({
                    "name": spec.get("name", "result"),
                    "template_type": spec.get("template", "str"),
                    "label": spec.get("description", ""),
                })
    except Exception as e:
        trace["steps"].append({"source": "llm_expand", "status": "failed", "error": str(e)})

    search_desc = expanded.description if expanded else description

    # 1. known-good 索引
    known_good_hits = _search_keywords(search_desc) if expanded else []
    if not known_good_hits:
        known_good_hits = _search_known_good(description)
    trace["steps"].append({
        "source": "known_good",
        "keywords_used": description[:60],
        "hits": len(known_good_hits),
        "top": known_good_hits[0]["source_name"] if known_good_hits else None,
    })

    trace_write("DRAFT", "known-good 检索",
        {"hits": len(known_good_hits), "top": known_good_hits[0] if known_good_hits else None})

    if known_good_hits:
        draft = known_good_hits[0]
        draft["output_description"] = description
        if input_templates:
            draft["input_templates"] = input_templates
        if output_templates:
            draft["output_templates"] = output_templates
        if expanded:
            draft["_expanded"] = {"name": expanded.name, "summary": expanded.summary, "description": expanded.description}
        draft["_trace"] = trace

        from quick_app.cli_detector import detect_tool
        if draft.get("source_type") == "cli_tool":
            installed = detect_tool(draft["source_name"])
            draft["installed"] = installed
            draft["install_status"] = "已安装" if installed else "未安装"
            if not installed and draft.get("install_hint"):
                draft["source_reason"] += f"。⚠ {draft['install_hint']}"

        return json.dumps(draft, ensure_ascii=False)

    # 2. GitHub 搜索（用 LLM 构造精确查询，含 qualifiers）
    from quick_app.github_search import search_github

    # 构造 LLM call 函数（复用 parent agent）
    gh_llm_call = None
    if _parent_agent and hasattr(_parent_agent, "gateway"):
        parent = _parent_agent
        def _gh_llm(prompt: str) -> str:
            resp = parent.gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                model=parent.model, max_tokens=500,
            )
            return resp.content or ""
        gh_llm_call = _gh_llm

    gh_results = search_github(
        description=description,
        limit=3,
        llm_call=gh_llm_call,
        keywords=expanded.keywords if expanded else None,
    )
    trace_write("DRAFT", "GitHub 搜索",
        {"results": len(gh_results), "top": gh_results[0] if gh_results else None})
    trace["steps"].append({
        "source": "github",
        "query": "(LLM 构造，日志中不可见)",  # 查询由 LLM 构造，不暴露原始 query
        "results": len(gh_results),
        "top": {"name": gh_results[0]["source_name"], "stars": gh_results[0]["stars"], "desc": gh_results[0]["description"][:60]} if gh_results else None,
    })

    if gh_results:
        best = gh_results[0]
        draft = {
            "name": _infer_name(best["source_name"], description),
            "description": best["description"],
            "source_type": "github",
            "source_name": best["source_name"],
            "source_reason": best.get("reason", "GitHub 搜索结果"),
            "params": expanded.input_spec if expanded and expanded.input_spec else [],
            "install_hint": f"GitHub: {best.get('url', '')}",
            "output_description": description,
            "venv_level": VenvLevel.ISOLATED.value,
            "stars": best.get("stars", 0),
            "github_url": best.get("url", ""),
            "_trace": trace,
        }
        return json.dumps(_enrich_draft(draft, input_templates, output_templates, expanded), ensure_ascii=False)

    # 3. PyPI 搜索
    from quick_app.pip_index import search_pypi
    pip_results = search_pypi(description, limit=3)
    trace["steps"].append({
        "source": "pypi",
        "query": description[:40],
        "results": len(pip_results),
        "top": pip_results[0]["source_name"] if pip_results else None,
    })

    if pip_results:
        best = pip_results[0]
        draft = {
            "name": _infer_name(best["source_name"], description),
            "description": best.get("description", ""),
            "source_type": "pip_package",
            "source_name": best["source_name"],
            "source_reason": f"PyPI 搜索结果（{best.get('version', '')}）",
            "params": expanded.input_spec if expanded and expanded.input_spec else [],
            "install_hint": f"pip install {best['source_name']}",
            "output_description": description,
            "venv_level": VenvLevel.SHARED.value,
            "_trace": trace,
        }
        return json.dumps(_enrich_draft(draft, input_templates, output_templates, expanded), ensure_ascii=False)

    # 4. 全部无匹配 → write_code
    trace["steps"].append({"source": "write_code", "reason": "known-good/GitHub/PyPI 均无匹配"})
    base_draft = {
        "name": _infer_name("", description),
        "description": description,
        "source_type": "write_code",
        "source_name": "python",
        "source_reason": "已知索引/GitHub/PyPI 中均无匹配方案，需要自编写代码",
        "params": [],
        "install_hint": "",
        "output_description": description,
        "venv_level": VenvLevel.SHARED.value,
        "_trace": trace,
    }
    return json.dumps(_enrich_draft(base_draft, input_templates, output_templates, expanded), ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# create_app — 双重校验 + 代码生成 + 自我验证 + 注册
# ═══════════════════════════════════════════════════════════════


def _build_sample_args(draft: Draft) -> dict[str, Any]:
    """从 draft 参数构建样例参数字段。

    对 CLI 工具，参数可能指向实际文件。create_app 使用这些参数做验证时，
    不会真的跑（因为没文件），但如果工具至少能解析参数不崩溃就算通过。
    更严谨的验证由 Agent 在后续交互中完成。
    """
    samples = {}
    for p in draft.params:
        pname = p["name"]
        ptype = p.get("type", "string").lower()
        if ptype in ("integer", "int"):
            samples[pname] = 1
        elif ptype in ("number", "float"):
            samples[pname] = 1.0
        elif ptype == "boolean":
            samples[pname] = True
        elif ptype == "array":
            samples[pname] = []
        elif ptype == "object":
            samples[pname] = {}
        else:
            samples[pname] = "test"
    return samples


def _enrich_draft(draft: dict, input_tpl: list, output_tpl: list, expanded: Any = None) -> dict:
    """给 draft dict 注入模板和展开信息。"""
    if input_tpl:
        draft["input_templates"] = input_tpl
    if output_tpl:
        draft["output_templates"] = output_tpl
    if expanded:
        draft["_expanded"] = {"name": expanded.name, "summary": expanded.summary, "description": expanded.description}
    return draft


def _check_source_validity(draft: Draft) -> str | None:
    """双重校验：检查 source_type=write_code 时是否真的有匹配的已知方案。

    如果有匹配方案但不使用，返回错误消息。
    """
    if draft.source_type != SourceType.WRITE_CODE:
        return None  # 不是自写代码场景，无需校验

    # 重新搜索 known-good
    for entry in KNOWN_GOOD_INDEX:
        score = _match_keywords(draft.description, entry.keywords)
        if score > 0:
            return (
                f"known-good 索引中有匹配方案「{entry.name}」（匹配度 {score}），"
                f"不应自写代码。请重新调用 get_quick_app_draft 获取推荐方案。"
            )
    return None


def _llm_generate_code(draft: Draft) -> str:
    """生成 handler 代码。

    优先级：known-good handler_pattern > LLM 生成 > skeleton 兜底。
    对于 known-good 索引中有 handler_pattern 的工具（如 wttr.in/ffmpeg 等），
    直接使用预置 pattern，不调 LLM（避免 LLM 产生空 body 或幻觉参数）。
    """
    # 1. 优先 known-good pattern（有 handler_pattern 的直接填空，不用 LLM）
    from quick_app.codegen import _find_matching_entry, _render_from_pattern
    entry = _find_matching_entry(draft)
    if entry and entry.handler_pattern:
        return _render_from_pattern(draft, entry)

    # 2. 没有 known-good pattern → 调 LLM
    parent = _parent_agent
    if parent is None or not hasattr(parent, "gateway"):
        logger.warning("create_app: parent_agent not wired, using skeleton")
        return generate_code(draft)

    prompt = _build_llm_code_prompt(draft)
    from quick_app.trace_log import write as tw
    tw("CODEGEN", f"LLM Prompt for {draft.name}", {"prompt": prompt, "model": getattr(parent, 'model', '?')})

    try:
        response = parent.gateway.chat(
            messages=[{"role": "user", "content": prompt}],
            model=parent.model,
            max_tokens=2000,
        )
        handler_body = _clean_llm_response((response.content or "").strip())
        tw("CODEGEN", f"LLM 响应 for {draft.name}", {"raw_length": len(response.content or ""), "body_preview": handler_body[:200]})
    except Exception as e:
        logger.warning("create_app: llm call failed: %s", e)
        return generate_code(draft)

    # 检查 LLM 返回是否非空
    if not handler_body.strip():
        logger.warning("create_app: llm returned empty body, using skeleton")
        return generate_code(draft)

    # 用模板包装
    from quick_app.codegen import _indent_body
    from quick_app.models import QUICK_APP_TEMPLATE

    # GitHub 项目需要添加本地 repo 到 sys.path
    if draft.source_type == SourceType.GITHUB:
        path_setup = (
            '# 添加本地克隆的 repo 到导入路径\n'
            'import sys, os\n'
            '_repo = os.path.join(os.path.dirname(__file__), "repo")\n'
            'if os.path.isdir(_repo):\n'
            '    sys.path.insert(0, _repo)\n'
            '\n'
        )
    else:
        path_setup = ""

    return QUICK_APP_TEMPLATE.format(
        name=draft.name,
        description=draft.description,
        handler_body=_indent_body(path_setup + handler_body),
    )


def _build_llm_code_prompt(draft: Draft) -> str:
    """构建 LLM 代码生成 prompt（要求生成 handler body 部分）。"""
    params_json = json.dumps(
        [{"name": p["name"], "type": p.get("type", "string"),
          "description": p.get("description", "")} for p in draft.params],
        ensure_ascii=False, indent=2,
    )
    lines = [
        "Generate the handler body for a QuickApp tool wrapper. No explanations.",
        "",
        f"# Tool",
        f"name: {draft.name}",
        f"description: {draft.description}",
        f"source: {draft.source_type.value} ({draft.source_name})",
        f"",
        f"# Parameters",
        f"{params_json}",
        f"",
        f"# Rules",
    ]

    if draft.source_type == SourceType.CLI_TOOL:
        lines.append("- Use subprocess.run() to wrap the CLI command")
        lines.append("- Check returncode, return user-friendly error on failure")
        lines.append("- Add timeout (60-120s)")
        lines.append(f"- Tool may need installation: {draft.install_hint or 'check if available'}")
    elif draft.source_type == SourceType.PIP_PACKAGE:
        lines.append("- Import packages inside the function")
        lines.append("- Add try/except error handling")
        lines.append(f"- Package: {draft.source_name}")
    else:
        lines.append("- Use standard library where possible")
        lines.append("- Add error handling")

    lines.extend([
        "",
        "Return ONLY the indented code that goes inside handler():",
        "  (starting at 4 spaces, like '    input = args[\"input\"]')",
        "",
        "Example for a file processing tool:",
        '    input_path = args["input"]',
        "    output_path = input_path + '.out'",
        "    return f'Generated {output_path}'",
        "",
        "Plain Python code, no markdown fences. No extra explanation.",
    ])

    return "\n".join(lines)


def _clean_llm_response(text: str) -> str:
    """清理 LLM 返回的代码，提取 handler body 部分。

    支持两种输出格式：
    1. 纯 body 代码（没有 def handler 行）
    2. 完整函数代码（包含 def handler 行）→ 提取 body
    """
    text = text.strip()
    # 移除 markdown 代码块标记
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 如果包含 def handler，提取 body（去掉函数签名和 @ 装饰器）
    if "def handler" in text:
        # 找到 def handler 行之后的内容
        after_def = text[text.index("def handler"):]
        lines = after_def.split("\n")
        # 跳过 def handler(...):
        body_lines = []
        found_colon = False
        for line in lines:
            if not found_colon:
                if line.strip().endswith(":"):
                    found_colon = True
                continue
            body_lines.append(line)
        text = "\n".join(body_lines).strip()

    # 移除可能的多余缩进
    if text:
        from textwrap import dedent
        text = dedent(text)
        text = text.strip()

    return text


def _handle_create(args: dict[str, Any]) -> str:
    """create_app 处理器。"""
    draft_data = args.get("draft")
    if not draft_data:
        return json.dumps({"error": "缺少 draft 参数"})

    if isinstance(draft_data, str):
        try:
            draft_data = json.loads(draft_data)
        except json.JSONDecodeError:
            return json.dumps({"error": "draft 参数不是有效的 JSON"})

    # ── 校验 draft 字段 ──
    required = ["name", "description", "source_type", "source_name"]
    for field in required:
        if field not in draft_data:
            return json.dumps({"error": f"draft 缺少必要字段：{field}"})

    try:
        source_type = SourceType(draft_data["source_type"])
    except ValueError:
        return json.dumps({"error": f"不支持的 source_type：{draft_data['source_type']}"})

    draft = Draft(
        name=draft_data["name"],
        description=draft_data["description"],
        source_type=source_type,
        source_name=draft_data["source_name"],
        source_reason=draft_data.get("source_reason", ""),
        params=draft_data.get("params", []),
        output_description=draft_data.get("output_description", ""),
        venv_level=VenvLevel(draft_data.get("venv_level", 0)),
        install_hint=draft_data.get("install_hint", ""),
    )

    # ── 双重校验：检查是否应使用已知方案 ──
    validation_error = _check_source_validity(draft)
    if validation_error:
        return json.dumps({"error": validation_error})

    # ── 生成代码 ──
    code = _llm_generate_code(draft)

    # ── 构建 schema ──
    schema = build_schema(draft.name, draft.description, draft.params)

    # ── 确定 venv 级别 ──
    if draft.venv_level == VenvLevel.NONE:
        venv_level = QuickAppManager.infer_venv_level(draft.source_type)
    else:
        venv_level = draft.venv_level

    # ── 创建（写文件 + 验证 + 注册）—— 最多重试 2 次 ──
    manager = get_tool_manager()
    sample_args = _build_sample_args(draft)

    last_error = ""
    for attempt in range(3):  # 最多 3 次
        try:
            # 第一次用 LLM 生成的代码，重试时重新生成
            if attempt > 0:
                code = _llm_generate_code(draft)

            qa = manager.create(
                name=draft.name,
                code=code,
                description=draft.description,
                schema=schema,
                source_type=draft.source_type,
                venv_level=venv_level,
                sample_args=sample_args,
                source_name=draft.source_name,
            )
            return json.dumps({
                "success": True,
                "name": draft.name,
                "message": f"✅ 已创建快应用「{draft.name}」——{draft.description}",
                "source": draft.source_type.value,
                "venv_level": venv_level.value,
            }, ensure_ascii=False)

        except RuntimeError as e:
            last_error = str(e)
            logger.warning("create_app attempt %d failed for %s: %s",
                           attempt + 1, draft.name, last_error)
            continue

    return json.dumps({
        "success": False,
        "error": f"创建失败（已尝试 3 次）：{last_error}",
        "hint": "可尝试调整参数后重新创建，或在 known-good 索引中查找替代方案",
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# list_apps / delete_app
# ═══════════════════════════════════════════════════════════════


def _handle_list(args: dict[str, Any]) -> str:
    """list_apps 处理器。"""
    manager = get_tool_manager()
    apps = manager.list()
    if not apps:
        return "当前没有已创建的快应用。"

    lines = [f"快应用列表（共 {len(apps)} 个）："]
    for qa in apps:
        source_label = {
            SourceType.CLI_TOOL: "CLI 工具",
            SourceType.PIP_PACKAGE: "pip 包",
            SourceType.GITHUB: "GitHub 项目",
            SourceType.WRITE_CODE: "自写代码",
        }.get(qa.source_type, qa.source_type.value)

        venv_label = {
            VenvLevel.NONE: "系统环境",
            VenvLevel.SHARED: "共享环境",
            VenvLevel.ISOLATED: "独立环境",
        }.get(qa.venv_level, f"级别 {qa.venv_level.value}")

        lines.append(
            f"  · {qa.name} — {qa.description} "
            f"({source_label}, {venv_label})"
        )

    return "\n".join(lines)


def _handle_delete(args: dict[str, Any]) -> str:
    """delete_app 处理器。"""
    name = args.get("name", "").strip()
    if not name:
        return json.dumps({"error": "缺少 name 参数"})

    manager = get_tool_manager()
    ok = manager.delete(name)
    if ok:
        return f"✅ 已删除快应用「{name}」"
    return json.dumps({"error": f"快应用「{name}」不存在"})


# ═══════════════════════════════════════════════════════════════
# 注册
# ═══════════════════════════════════════════════════════════════

_QUICK_APP_TOOLS = [
    {
        "name": "get_quick_app_draft",
        "schema": {
            "type": "function",
            "function": {
                "name": "get_quick_app_draft",
                "description": (
                    "检索可用的现有项目方案，返回创建快应用的草案卡片。\n\n"
                    "输入自然语言描述你想要的功能。工具会先查已知工具索引（CLI 工具和 pip 包），"
                    "返回最佳匹配方案。\n\n"
                    "收到草案后，确认方案合适再调 create_app。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "你想要的功能描述（如「做一个视频转 GIF 的工具」）",
                        },
                    },
                    "required": ["description"],
                },
            },
        },
        "handler": _handle_draft,
    },
    {
        "name": "create_app",
        "schema": {
            "type": "function",
            "function": {
                "name": "create_app",
                "description": (
                    "基于 draft 创建快应用。\n\n"
                    "接收 get_quick_app_draft 返回的草案卡片，自动完成：\n"
                    "  1. 生成包装代码\n"
                    "  2. 写文件到 .chips/quick_apps/\n"
                    "  3. 用样例参数自我验证\n"
                    "  4. 通过后注册到工具系统\n\n"
                    "验证不通过会自动重试（最多 3 次）。全部失败则返回错误。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "draft": {
                            "type": "object",
                            "description": "从 get_quick_app_draft 返回的完整草案卡片",
                        },
                    },
                    "required": ["draft"],
                },
            },
        },
        "handler": _handle_create,
    },
    {
        "name": "list_apps",
        "schema": {
            "type": "function",
            "function": {
                "name": "list_apps",
                "description": "列出所有已创建的快应用（名称、描述、来源、环境类型）。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        "handler": _handle_list,
    },
    {
        "name": "delete_app",
        "schema": {
            "type": "function",
            "function": {
                "name": "delete_app",
                "description": "删除一个快应用。会注销工具并删除文件，不可恢复。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "要删除的快应用名称",
                        },
                    },
                    "required": ["name"],
                },
            },
        },
        "handler": _handle_delete,
    },
]


def _register():
    for t in _QUICK_APP_TOOLS:
        registry.register(
            name=t["name"],
            toolset="quick_apps",
            schema=t["schema"],
            handler=t["handler"],
        )


_register()
