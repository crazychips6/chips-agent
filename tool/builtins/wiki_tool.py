"""Wiki 工具 — 个人知识库引擎的三个入口

架构：
  - wiki_ingest:  纯工具内聚（方案 1），内部调用 gateway 做 LLM 提取，不污染主 Agent 上下文
  - wiki_query:   细粒度工具（方案 2），LLM 在 ReAct 里自己调 read_index/read_page/search
  - wiki_lint:    纯工具内聚（方案 1），所有扫描在 handler 内部完成

用法（在 ReAct 循环中让 LLM 自动调用）：
  wiki_ingest → 处理所有新文件
  wiki_read_index → 看看知识库有什么
  wiki_read_page → 读具体一页
  wiki_search_pages → 搜关键词
  wiki_lint → 健康检查
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from tool.registry import registry

logger = logging.getLogger("chips.tool.wiki")


# ── Schema ──

WIKI_INGEST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wiki_ingest",
        "description": "将 raw/ 目录中的新文件摄入 wiki，由本地小模型自动提取概念/实体/决策等信息并生成知识页面。支持 --dry-run 预览不写入，--force 强制重新处理。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Wiki 项目根目录（默认 ~/.chips/wiki，可省略）",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "预览模式，不写文件",
                },
                "force": {
                    "type": "boolean",
                    "description": "强制重新处理所有文件而非仅新文件",
                },
            },
        },
    },
}

WIKI_READ_INDEX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wiki_read_index",
        "description": "读取 wiki 的 index.md 概览，了解知识库里有什么内容。返回分类别（concepts/sources/decisions/lessons）的条目列表。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Wiki 项目根目录（默认 ~/.chips/wiki，可省略）",
                },
            },
        },
    },
}

WIKI_READ_PAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wiki_read_page",
        "description": "读取 wiki 中指定页面的完整内容。页面路径格式如 concepts/self-attention 或 entities/xxx。",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "description": "页面路径，如 concepts/self-attention 或 decisions/use-postgres（不需要 .md 后缀）",
                },
                "path": {
                    "type": "string",
                    "description": "Wiki 项目根目录（默认 ~/.chips/wiki，可省略）",
                },
            },
            "required": ["page"],
        },
    },
}

WIKI_SEARCH_PAGES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wiki_search_pages",
        "description": "在 wiki 中按关键词搜索页面，返回匹配的页面名、路径和上下文片段。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "path": {
                    "type": "string",
                    "description": "Wiki 项目根目录（默认 ~/.chips/wiki，可省略）",
                },
            },
            "required": ["keyword"],
        },
    },
}

WIKI_LINT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wiki_lint",
        "description": "对 wiki 做健康检查：断链检测、孤儿页面、索引一致性、过期页面、可能的内容矛盾。返回 Markdown 格式报告。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Wiki 项目根目录（默认 ~/.chips/wiki，可省略）",
                },
                "skip_contradictions": {
                    "type": "boolean",
                    "description": "跳过 LLM 矛盾检测（节省 token）",
                },
                "fix": {
                    "type": "boolean",
                    "description": "自动修复可修复的问题（如创建缺失的断链目标页面）",
                },
            },
        },
    },
}


# ── Handlers ──


def _handle_ingest(args: dict[str, Any]) -> str:
    from tool.builtins.agent_tools import get_parent

    parent = get_parent()
    if parent is None:
        return json.dumps({"error": "parent agent not initialized"})

    wiki_dir = args.get("path") or os.getenv("CHIPS_WIKI_DIR") or os.path.expanduser("~/.chips/wiki")
    dry_run = args.get("dry_run", False)
    force = args.get("force", False)

    from wiki.ingest import run_ingest

    gateway = getattr(parent, "gateway", None)
    if gateway is None:
        return "❌ 无法访问 LLM gateway"

    try:
        result = run_ingest(
            gateway=gateway,
            model=parent.model,
            wiki_dir=wiki_dir,
            dry_run=dry_run,
            force=force,
        )
        return result
    except Exception as e:
        logger.error("wiki_ingest_failed: %s", e, exc_info=True)
        return f"❌ ingest 失败: {e}"


def _handle_read_index(args: dict[str, Any]) -> str:
    wiki_dir = args.get("path") or os.getenv("CHIPS_WIKI_DIR") or os.path.expanduser("~/.chips/wiki")

    from wiki.query import read_index

    content = read_index(wiki_dir)
    if not content:
        return "📭 Wiki 索引为空，请先执行 ingest。"
    return content


def _handle_read_page(args: dict[str, Any]) -> str:
    page = args.get("page", "")
    if not page:
        return "⚠️ 请指定页面路径，如 `page: concepts/self-attention`"

    wiki_dir = args.get("path") or os.getenv("CHIPS_WIKI_DIR") or os.path.expanduser("~/.chips/wiki")

    from wiki.query import read_page

    content = read_page(page, wiki_dir)
    if content is None:
        return f"❌ 页面不存在: `{page}`"
    return content


def _handle_search_pages(args: dict[str, Any]) -> str:
    keyword = args.get("keyword", "")
    if not keyword:
        return "⚠️ 请指定搜索关键词"

    wiki_dir = args.get("path") or os.getenv("CHIPS_WIKI_DIR") or os.path.expanduser("~/.chips/wiki")

    from wiki.query import search_pages

    results = search_pages(keyword, wiki_dir)
    if not results:
        return f"📭 未找到包含「{keyword}」的页面。"

    lines = [f"🔍 找到 {len(results)} 个包含「{keyword}」的页面：\n"]
    for r in results[:15]:
        lines.append(f"- [[{r['path']}]] — {r['snippet']}")
    if len(results) > 15:
        lines.append(f"\n...还有 {len(results) - 15} 个未显示")
    return "\n".join(lines)


def _handle_lint(args: dict[str, Any]) -> str:
    from tool.builtins.agent_tools import get_parent

    parent = get_parent()
    wiki_dir = args.get("path") or os.getenv("CHIPS_WIKI_DIR") or os.path.expanduser("~/.chips/wiki")
    skip = args.get("skip_contradictions", True)
    auto_fix = args.get("fix", False)

    from wiki.lint import run_lint

    gateway = getattr(parent, "gateway", None) if not skip else None

    try:
        report = run_lint(
            gateway=gateway,
            model=parent.model if parent else None,
            wiki_dir=wiki_dir,
            skip_contradictions=skip,
            auto_fix=auto_fix,
        )
        return report
    except Exception as e:
        logger.error("wiki_lint_failed: %s", e, exc_info=True)
        return f"❌ Lint 失败: {e}"


# ── 注册 ──

registry.register(
    name="wiki_ingest",
    toolset="wiki",
    schema=WIKI_INGEST_SCHEMA,
    handler=_handle_ingest,
    model_scope="all",
)

registry.register(
    name="wiki_read_index",
    toolset="wiki",
    schema=WIKI_READ_INDEX_SCHEMA,
    handler=_handle_read_index,
    model_scope="all",
)

registry.register(
    name="wiki_read_page",
    toolset="wiki",
    schema=WIKI_READ_PAGE_SCHEMA,
    handler=_handle_read_page,
    model_scope="all",
)

registry.register(
    name="wiki_search_pages",
    toolset="wiki",
    schema=WIKI_SEARCH_PAGES_SCHEMA,
    handler=_handle_search_pages,
    model_scope="all",
)

registry.register(
    name="wiki_lint",
    toolset="wiki",
    schema=WIKI_LINT_SCHEMA,
    handler=_handle_lint,
    model_scope="all",
)
