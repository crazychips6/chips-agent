"""通用文件 I/O 工具

提供 wiki 项目所需的文件读写、路径处理、元数据解析功能。
零外部依赖。
"""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

# ── 读写 ──


def read_md(path: str) -> str:
    """读取 markdown 文件内容。"""
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_md(path: str, content: str):
    """写入 markdown 文件，自动创建父目录。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def list_md_files(directory: str) -> list[str]:
    """递归列出目录下所有 .md 文件，返回绝对路径。"""
    results: list[str] = []
    if not os.path.isdir(directory):
        return results
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if f.endswith(".md"):
                results.append(os.path.join(root, f))
    return sorted(results)


def file_exists(path: str) -> bool:
    return os.path.isfile(path)


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


# ── Frontmatter ──


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """解析 Markdown 前置元数据，返回 (metadata, body)。

    支持 --- 分隔的 YAML-like frontmatter。
    """
    metadata: dict[str, Any] = {}
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return metadata, content

    end = stripped.find("---", 3)
    if end == -1:
        return metadata, content

    block = stripped[3:end].strip()
    body = stripped[end + 3 :].lstrip()

    for line in block.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            # 解析日期
            if val and val[0].isdigit() and "-" in val:
                try:
                    from datetime import date as dt_date

                    parts = val.split("-")
                    if len(parts) == 3:
                        metadata[key] = dt_date(int(parts[0]), int(parts[1]), int(parts[2]))
                        continue
                except (ValueError, IndexError):
                    pass
            metadata[key] = val

    return metadata, body


# ── Wikilink 处理 ──

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def extract_wikilinks(content: str) -> list[str]:
    """提取所有 [[wikilink]] 目标名。"""
    return WIKILINK_RE.findall(content)


def normalize_filename(name: str) -> str:
    """转为 kebab-case 文件名。

    "Transformer Architecture" → "transformer-architecture"
    """
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9一-鿿]+", "-", name)
    name = name.strip("-")
    name = re.sub(r"-+", "-", name)
    if not name:
        name = "untitled"
    return name


def wikilink_to_path(wiki_dir: str, link_target: str) -> str:
    """将 [[wikilink]] 转换为页面路径。

    [[self-attention]] → {wiki_dir}/concepts/self-attention.md
    [[sources/transformer]] → {wiki_dir}/sources/transformer.md
    """
    # 如果链接已经带路径前缀，直接使用
    if "/" in link_target:
        page_type, _, slug = link_target.partition("/")
        return os.path.join(wiki_dir, page_type, f"{slug}.md")

    # 无前缀 → 搜索所有页面
    for subdir in ("concepts", "entities", "sources", "decisions", "lessons", "answers"):
        candidate = os.path.join(wiki_dir, subdir, f"{link_target}.md")
        if os.path.isfile(candidate):
            return candidate

    # 找不到 → 最可能的概念页面
    return os.path.join(wiki_dir, "concepts", f"{link_target}.md")


def page_type_from_path(page_path: str, wiki_dir: str) -> str:
    """根据文件路径推断页面类型。"""
    rel = os.path.relpath(page_path, wiki_dir)
    parts = rel.split(os.sep)
    if len(parts) >= 2:
        dir_name = parts[0]
        from wiki.config import PAGE_TYPES

        return PAGE_TYPES.get(dir_name, "unknown")
    return "unknown"


def ensure_wiki_structure(pages_dir: str):
    """确保 wiki 目录结构存在。"""
    wiki_root = os.path.dirname(pages_dir)
    for subdir in ("concepts", "entities", "sources", "decisions", "lessons", "answers"):
        ensure_dir(os.path.join(pages_dir, subdir))
    ensure_dir(os.path.join(wiki_root, "raw", "articles"))
    ensure_dir(os.path.join(wiki_root, "raw", "journal"))
    ensure_dir(os.path.join(wiki_root, "templates"))

    today_str = date.today().isoformat()
    index_path = os.path.join(pages_dir, "index.md")
    if not file_exists(index_path):
        write_md(index_path, _DEFAULT_INDEX.format(today=today_str))

    log_path = os.path.join(pages_dir, "log.md")
    if not file_exists(log_path):
        write_md(log_path, f"# Operation Log\n\n[{today_str}] INIT — Wiki 仓库初始化\n")


_DEFAULT_INDEX = """# Wiki Index

> 主索引 — 每个条目格式：`- [[page-name]] — 一句话摘要 (YYYY-MM-DD)`
> 最后更新：{today}

## Concepts
（暂无条目）

## Entities
（暂无条目）

## Sources
（暂无条目）

## Decisions
（暂无条目）

## Lessons
（暂无条目）

## Answers
（暂无条目）
"""
