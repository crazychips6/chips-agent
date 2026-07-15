"""Query 工具 — 知识检索与综合回答

提供两个核心函数：
  relevant_pages(question, wiki_dir) — 从 index.md 选取相关页面
  synthesize(question, page_contents, gateway, model) — 综合回答

注意：query 不直接提供"综合"入口，而是暴露细粒度函数给工具调用。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from wiki.config import get_wiki_pages_dir
from wiki.files import read_md, list_md_files
from wiki.prompts import ROUTE_QUESTION, SYNTHESIZE_ANSWER

logger = logging.getLogger("chips.wiki.query")


def read_index(wiki_dir: str) -> str:
    """读取 index.md 内容。"""
    path = os.path.join(get_wiki_pages_dir(wiki_dir), "index.md")
    if not os.path.isfile(path):
        return ""
    return read_md(path)


def read_page(page_path: str, wiki_dir: str) -> str | None:
    """读取指定 wiki 页面的内容。

    Args:
        page_path: 可以是 "concepts/self-attention" 或 "concepts/self-attention.md"
        wiki_dir: wiki 项目根目录

    Returns:
        页面内容的 Markdown 文本，或 None（页面不存在）
    """
    pages_dir = get_wiki_pages_dir(wiki_dir)
    page_path = page_path.rstrip(".md")
    path = os.path.join(pages_dir, f"{page_path}.md")
    if not os.path.isfile(path):
        return None
    return read_md(path)


def search_pages(keyword: str, wiki_dir: str) -> list[dict[str, str]]:
    """按关键词搜索 wiki 页面。

    Returns:
        [{name, path, snippet}, ...]
    """
    pages_dir = get_wiki_pages_dir(wiki_dir)
    results: list[dict[str, str]] = []
    keyword_lower = keyword.lower()

    for md_path in list_md_files(pages_dir):
        # 排除 index.md 和 log.md
        rel = os.path.relpath(md_path, pages_dir)
        if rel in ("index.md", "log.md"):
            continue
        try:
            content = read_md(md_path)
            if keyword_lower in content.lower():
                # 找到关键词所在行作为 snippet
                snippet = ""
                for line in content.split("\n"):
                    if keyword_lower in line.lower():
                        snippet = line.strip()[:120]
                        break
                name = os.path.splitext(os.path.basename(md_path))[0]
                results.append({
                    "name": name,
                    "path": rel,
                    "snippet": snippet or content[:120],
                })
        except Exception:
            continue

    return results


def route_question(question: str, index_content: str, gateway, model: str) -> list[str]:
    """调用 LLM 根据 index.md 选取相关问题页面。

    Returns:
        相关页面路径列表，如 ["concepts/self-attention", "sources/transformer"]
    """
    if not index_content:
        return []

    prompt = ROUTE_QUESTION.format(index_content=index_content, question=question)
    messages = [
        {"role": "system", "content": "你是一个知识库路由员。只输出 JSON。"},
        {"role": "user", "content": prompt},
    ]

    try:
        result = gateway.chat(messages=messages, model=model, max_tokens=1024)
        raw = result.content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
        parsed = json.loads(raw)
        pages = parsed.get("relevant_pages", [])
        logger.info("wiki_route_question pages=%s", pages)
        return pages
    except Exception as e:
        logger.warning("wiki_route_failed: %s", e)
        return []


def synthesize_answer(
    question: str,
    page_contents: dict[str, str],
    gateway,
    model: str,
) -> str:
    """综合多页面内容生成回答。"""
    if not page_contents:
        return "知识库中没有找到相关信息。"

    formatted = "\n\n---\n\n".join(
        f"## {path}\n{content[:3000]}"
        for path, content in page_contents.items()
    )

    prompt = SYNTHESIZE_ANSWER.format(
        question=question,
        page_contents=formatted,
    )
    messages = [
        {"role": "system", "content": "你是一个知识综合回答员。引用用 [[wikilink]] 格式。"},
        {"role": "user", "content": prompt},
    ]

    try:
        result = gateway.chat(messages=messages, model=model, max_tokens=4096)
        return result.content.strip()
    except Exception as e:
        logger.warning("wiki_synthesize_failed: %s", e)
        return "回答生成失败，请稍后重试。"
