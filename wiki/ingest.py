"""Ingest 流水线 — 文件检测 → LLM 提取 → 页面生成 → 链接 → 更新

入口：
  run_ingest(parent_gateway, parent_model, wiki_dir=None)
  返回摘要字符串
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from typing import Any

from wiki.config import get_wiki_dir, get_raw_dir, get_wiki_pages_dir, RAW_TYPE_MAP, PAGE_TYPES
from wiki.files import (
    read_md,
    write_md,
    file_exists,
    ensure_dir,
    list_md_files,
    normalize_filename,
    extract_wikilinks,
)
from wiki.prompts import get_extract_prompt

logger = logging.getLogger("chips.wiki.ingest")

# ── 公开入口 ──


def run_ingest(
    gateway,
    model: str,
    wiki_dir: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> str:
    """执行完整 ingest 流程。

    Args:
        gateway: chips 的 ModelGateway 实例（供 LLM 调用）
        model: 使用的模型名
        wiki_dir: wiki 项目根目录（默认 ~/.chips/wiki）
        dry_run: 预览模式，不写文件
        force: 强制重新处理所有文件

    Returns:
        处理摘要（展示给用户）
    """
    base_dir = wiki_dir or get_wiki_dir()
    raw_dir = get_raw_dir(base_dir)
    pages_dir = get_wiki_pages_dir(base_dir)

    # 1. 检测新文件
    new_files = _detect_new_files(raw_dir, pages_dir, force)
    if not new_files:
        return "📭 没有发现新文件需要处理。"

    total_created = 0
    total_updated = 0
    errors: list[str] = []
    file_results: list[str] = []

    for file_path in new_files:
        result = _process_one_file(file_path, gateway, model, pages_dir, dry_run)
        if result is None:
            continue
        file_results.append(result)  # 存完整 dict
        total_created += result["created"]
        total_updated += result["updated"]
        if result.get("error"):
            errors.append(result["error"])
        time.sleep(0.3)  # API 限速保护

    # 汇总
    if dry_run:
        lines = ["🔍 预览模式 — 以下文件将被处理："]
        lines.extend(f"  📄 {os.path.basename(f)}" for f in new_files)
        lines.append("")
        for r in file_results:
            if r.get("_preview_pages"):
                pages = ", ".join(r["_preview_pages"])
                lines.append(f"  → 将创建 {pages}")
        return "\n".join(lines)

    summary_parts = []
    if total_created > 0 or total_updated > 0:
        summary_parts.append(f"📝 处理了 {len(new_files)} 个文件")
        summary_parts.append(f"  新建 {total_created} 个页面，更新 {total_updated} 个页面")
    for r in file_results:
        summary = r.get("summary", "")
        if summary:
            summary_parts.append(f"  ✅ {summary}")
    if errors:
        summary_parts.append(f"  ⚠️ {len(errors)} 个错误")
        for e in errors[:3]:
            summary_parts.append(f"    {e}")
    return "\n".join(summary_parts)


# ── 文件检测 ──


def _detect_new_files(raw_dir: str, pages_dir: str, force: bool) -> list[str]:
    """检测 raw/ 中未处理的新文件。

    通过比对 log.md 中的记录来去重。
    """
    log_path = os.path.join(pages_dir, "log.md")
    processed: set[str] = set()

    if not force and file_exists(log_path):
        log_content = read_md(log_path)
        for line in log_content.split("\n"):
            if "INGEST" in line and "raw/" in line:
                # 从日志行提取文件名
                # [2026-07-15 14:30] INGEST raw/articles/foo.md → concepts/foo ...
                parts = line.split()
                for p in parts:
                    if p.startswith("raw/") or (p.startswith("raw") and "/" in p):
                        processed.add(os.path.basename(p))

    raw_files: list[str] = []
    for root, _dirs, files in os.walk(raw_dir):
        for f in sorted(files):
            if f.endswith((".md", ".txt", ".mdx")):
                if force or f not in processed:
                    raw_files.append(os.path.join(root, f))

    return raw_files


# ── 单文件处理 ──


def _classify_file(file_path: str) -> str:
    """根据文件路径判断类型。"""
    rel = os.path.relpath(file_path, os.path.dirname(os.path.dirname(file_path)))
    parts = rel.split(os.sep)
    for part in parts:
        if part in RAW_TYPE_MAP:
            return RAW_TYPE_MAP[part]
    return "article"


def _process_one_file(
    file_path: str,
    gateway,
    model: str,
    pages_dir: str,
    dry_run: bool,
) -> dict | None:
    """处理单个文件。"""
    file_type = _classify_file(file_path)
    file_name = os.path.basename(file_path)

    logger.info("wiki_ingest file=%s type=%s", file_name, file_type)

    # 读取
    try:
        content = read_md(file_path)
    except Exception as e:
        logger.warning("wiki_read_failed file=%s error=%s", file_name, e)
        return {"summary": f"❌ 读取失败: {file_name}", "created": 0, "updated": 0, "error": str(e)}

    # LLM 提取
    extracted = _call_llm_extract(content, file_type, gateway, model)
    if extracted is None:
        return {"summary": f"❌ 提取失败: {file_name}", "created": 0, "updated": 0, "error": "LLM 提取返回空"}

    created = 0
    updated = 0
    pages_created: list[str] = []

    if dry_run:
        pages_created = _preview_pages(extracted, file_type, file_name)
        return {
            "summary": f"预览: {file_name}",
            "created": 0,
            "updated": 0,
            "_preview_pages": pages_created,
        }

    # 生成 source 页面
    source_slug = _generate_source_page(extracted, file_type, file_path, pages_dir)
    if source_slug:
        created += 1
        pages_created.append(f"sources/{source_slug}.md")

    # 更新 index.md
    _update_index(extracted, source_slug, pages_dir)

    # 生成 concept 页面
    for c in extracted.get("concepts", []):
        slug = c.get("slug", normalize_filename(c.get("name", "")))
        path = os.path.join(pages_dir, "concepts", f"{slug}.md")
        if file_exists(path):
            _append_to_concept_page(path, c, source_slug)
            updated += 1
        else:
            _create_concept_page(path, c, source_slug)
            created += 1
        pages_created.append(f"concepts/{slug}.md")

    # 生成 entity 页面
    for e in extracted.get("entities", []):
        slug = e.get("slug", normalize_filename(e.get("name", "")))
        path = os.path.join(pages_dir, "entities", f"{slug}.md")
        if file_exists(path):
            updated += 1
        else:
            _create_entity_page(path, e, source_slug)
            created += 1
        pages_created.append(f"entities/{slug}.md")

    # journal 特有的 decisions / lessons
    decisions = extracted.get("decisions", [])
    for d in decisions:
        slug = normalize_filename(d.get("title", ""))
        path = os.path.join(pages_dir, "decisions", f"{slug}.md")
        if not file_exists(path):
            _create_decision_page(path, d, source_slug)
            created += 1
            pages_created.append(f"decisions/{slug}.md")

    lessons = extracted.get("lessons", [])
    for lsn in lessons:
        slug = normalize_filename(lsn.get("title", ""))
        path = os.path.join(pages_dir, "lessons", f"{slug}.md")
        if not file_exists(path):
            _create_lesson_page(path, lsn, source_slug)
            created += 1
            pages_created.append(f"lessons/{slug}.md")

    # 交叉链接
    _cross_link_pages(pages_created, pages_dir)

    # 追加 log.md
    _append_log(file_path, pages_created, pages_dir)

    summary = f"{os.path.basename(file_path)} → {len(pages_created)} 页"
    logger.info("wiki_ingest_done file=%s created=%d updated=%d pages=%s",
                file_name, created, updated, pages_created)
    return {"summary": summary, "created": created, "updated": updated}


# ── LLM 提取 ──


def _call_llm_extract(content: str, file_type: str, gateway, model: str) -> dict | None:
    """调用 LLM 提取结构化信息。"""
    prompt = get_extract_prompt(file_type)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"从以下内容中提取结构化信息：\n\n{content[:8000]}"},
    ]

    for attempt in range(3):
        try:
            result = gateway.chat(messages=messages, model=model, max_tokens=4096)
            raw = result.content.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("not a dict")
            return parsed
        except Exception as e:
            logger.warning("wiki_extract_retry attempt=%d error=%s", attempt + 1, e)
            if attempt == 2:
                return None
    return None


# ── 预览 ──


def _preview_pages(extracted: dict, file_type: str, file_name: str) -> list[str]:
    """预览模式下返回将要创建的页面列表。"""
    pages: list[str] = [f"sources/{normalize_filename(extracted.get('title', file_name))}.md"]
    for c in extracted.get("concepts", []):
        pages.append(f"concepts/{c.get('slug', normalize_filename(c.get('name', '')))}.md")
    for e in extracted.get("entities", []):
        pages.append(f"entities/{e.get('slug', normalize_filename(e.get('name', '')))}.md")
    for d in extracted.get("decisions", []):
        pages.append(f"decisions/{normalize_filename(d.get('title', ''))}.md")
    for lsn in extracted.get("lessons", []):
        pages.append(f"lessons/{normalize_filename(lsn.get('title', ''))}.md")
    return pages


# ── 页面生成 ──


def _generate_source_page(extracted: dict, file_type: str, source_path: str, pages_dir: str) -> str:
    """生成来源页面。返回 slug。"""
    title = extracted.get("title", "Untitled")
    slug = extracted.get("slug", normalize_filename(title))
    today = date.today().isoformat()
    summary = extracted.get("summary", "")
    concepts = extracted.get("concepts", [])
    entities = extracted.get("entities", [])
    quotes = extracted.get("quotes", [])

    concept_links = "\n".join(
        f"- [[concepts/{c['slug']}]] — {c.get('definition', '')}"
        for c in concepts if c.get('slug')
    )
    entity_links = "\n".join(
        f"- [[entities/{e['slug']}]] — {e.get('description', '')}"
        for e in entities if e.get('slug')
    )
    quote_lines = "\n".join(f"> {q}" for q in quotes)

    # 论文额外字段
    methodology = extracted.get("methodology", "")
    findings = extracted.get("findings", [])

    lines = [f"# {title}", f"**类型**: {file_type}", f"**来源路径**: {source_path}", f"**处理日期**: {today}"]

    # 来源类型前缀
    type_label = {"article": "文章/笔记", "journal": "日记/日志", "paper": "论文/报告"}.get(file_type, file_type)
    lines.insert(2, f"**来源类型**: {type_label}")

    lines.extend(["", "## 一句话摘要", summary])

    if concept_links:
        lines.extend(["", "## 提取的关键概念", concept_links])

    if entity_links:
        lines.extend(["", "## 提取的实体", entity_links])

    if methodology:
        lines.extend(["", "## 核心方法", methodology])

    if findings:
        lines.extend(["", "## 核心发现"] + [f"- {f}" for f in findings])

    if quote_lines:
        lines.extend(["", "## 提取的引用/金句", quote_lines])

    lines.extend(["", "## 笔记", "（处理时的额外观察和思考）", ""])

    path = os.path.join(pages_dir, "sources", f"{slug}.md")
    _safe_write(path, "\n".join(lines))
    return slug


def _create_concept_page(path: str, concept: dict, source_slug: str):
    """创建概念页面。"""
    today = date.today().isoformat()
    name = concept.get("name", "未命名概念")
    definition = concept.get("definition", "")
    details = concept.get("details", "")
    source_context = concept.get("source_context", "")

    content = f"""# {name}
**类型**: concept
**创建**: {today}
**更新**: {today}
**来源**: [[sources/{source_slug}]]

## 定义
{definition}

## 详细说明
{details}

## 关键要点
- {definition}

## 相关概念
（暂无关联概念）

## 来源引用
- [[sources/{source_slug}]] — {source_context[:200] if source_context else "提取位置"}
"""
    write_md(path, content.strip() + "\n")


def _append_to_concept_page(path: str, concept: dict, source_slug: str):
    """在已有概念页面追加新来源的记录。"""
    today = date.today().isoformat()
    existing = read_md(path)
    name = concept.get("name", "")
    source_context = concept.get("source_context", "")

    # 更新日期
    existing = existing.replace("**更新**: ", f"**更新**: {today}\n# **更新**", 1) if "**更新**: " in existing else existing

    append = f"""
## 来源引用（追加于 {today}）
- [[sources/{source_slug}]] — {source_context[:200] if source_context else "提取位置"}
"""
    write_md(path, existing.rstrip() + append)


def _create_entity_page(path: str, entity: dict, source_slug: str):
    """创建实体页面。"""
    today = date.today().isoformat()
    name = entity.get("name", "未命名实体")
    category = entity.get("category", "person")
    description = entity.get("description", "")

    content = f"""# {name}
**类型**: entity
**分类**: {category}
**创建**: {today}
**更新**: {today}

## 简介
{description}

## 关键信息
- **分类**: {category}

## 相关概念
（暂无关联概念）

## 相关实体
（暂无关联实体）

## 来源引用
- [[sources/{source_slug}]] — 首次提及
"""
    write_md(path, content.strip() + "\n")


def _create_decision_page(path: str, decision: dict, source_slug: str):
    """创建决策页面。"""
    today = date.today().isoformat()
    title = decision.get("title", "未命名决策")
    context = decision.get("context", "")
    rationale = decision.get("rationale", "")
    alternatives = decision.get("alternatives", "")

    content = f"""# {title}
**类型**: decision
**创建**: {today}
**更新**: {today}
**来源**: [[sources/{source_slug}]]

## 决策背景
{context}

## 选择理由
{rationale}

## 备选方案
{alternatives}

## 来源引用
- [[sources/{source_slug}]] — 决策来源
"""
    write_md(path, content.strip() + "\n")


def _create_lesson_page(path: str, lesson: dict, source_slug: str):
    """创建经验教训页面。"""
    today = date.today().isoformat()
    title = lesson.get("title", "未命名经验")
    problem = lesson.get("problem", "")
    solution = lesson.get("solution", "")
    key_takeaway = lesson.get("key_takeaway", "")

    content = f"""# {title}
**类型**: lesson
**创建**: {today}
**更新**: {today}
**来源**: [[sources/{source_slug}]]

## 遇到的问题
{problem}

## 解决方法
{solution}

## 核心收获
{key_takeaway}

## 来源引用
- [[sources/{source_slug}]] — 经验来源
"""
    write_md(path, content.strip() + "\n")


# ── 交叉链接 ──


def _cross_link_pages(created_pages: list[str], pages_dir: str):
    """在新建页面间建立双向 [[wikilink]]。"""
    for page_path in created_pages:
        full_path = os.path.join(pages_dir, page_path)
        if not file_exists(full_path):
            continue
        content = read_md(full_path)
        existing_links = extract_wikilinks(content)
        # 寻找已创建页面中尚未被引用的
        for other in created_pages:
            if other == page_path:
                continue
            # other 是 "concepts/slug.md" 格式
            link_target = other.replace(".md", "")
            if link_target not in existing_links:
                # 追加到相关概念/来源引用区域
                section_header = _find_link_section(content, other)
                if section_header:
                    # 找到合适的位置插入链接
                    # 简单策略：追加到 ## 相关概念 或 ## 来源引用
                    content = _insert_wikilink(content, link_target, section_header)

        write_md(full_path, content)


def _find_link_section(content: str, page_path: str) -> str | None:
    """根据页面路径找到合适的链接插入区域。"""
    if page_path.startswith("concepts/"):
        return "## 相关概念"
    if page_path.startswith("entities/"):
        return "## 相关实体"
    if page_path.startswith("sources/"):
        return "## 来源引用"
    if page_path.startswith("decisions/"):
        return "## 来源引用"
    if page_path.startswith("lessons/"):
        return "## 来源引用"
    return "## 相关概念"


def _insert_wikilink(content: str, link_target: str, section_header: str) -> str:
    """在指定区域后插入 wikilink。"""
    marker = f"{section_header}\n"
    if marker in content:
        entry = f"- [[{link_target}]] — 关联页面\n"
        # 在 section 的最后一项之后插入
        lines = content.split("\n")
        new_lines = []
        in_section = False
        inserted = False
        for i, line in enumerate(lines):
            new_lines.append(line)
            if line == section_header:
                in_section = True
            elif in_section and (line.startswith("#") or line.startswith("---")):
                # 下一个 section 开始，在前一行插入
                new_lines.insert(-1 if new_lines[-1].strip() == "" else len(new_lines) - 1, entry)
                inserted = True
                in_section = False
            elif in_section and i == len(lines) - 1:
                new_lines.append(entry)
                inserted = True
        if inserted:
            return "\n".join(new_lines)
    return content


# ── Index 更新 ──


def _update_index(extracted: dict, source_slug: str, pages_dir: str):
    """在 index.md 中追加新条目。"""
    index_path = os.path.join(pages_dir, "index.md")
    if not file_exists(index_path):
        return
    today = date.today().isoformat()
    summary = extracted.get("summary", "")
    title = extracted.get("title", "Untitled")

    with open(index_path, "a", encoding="utf-8") as f:
        # 来源条目
        f.write(f"- [[sources/{source_slug}]] — {summary} ({today})\n")
        # 概念条目
        for c in extracted.get("concepts", []):
            slug = c.get("slug", normalize_filename(c.get("name", "")))
            defn = c.get("definition", "")
            f.write(f"- [[concepts/{slug}]] — {defn} ({today})\n")
        # 实体条目
        for e in extracted.get("entities", []):
            slug = e.get("slug", normalize_filename(e.get("name", "")))
            desc = e.get("description", "")
            f.write(f"- [[entities/{slug}]] — {desc} ({today})\n")
        # 决策条目
        for d in extracted.get("decisions", []):
            slug = normalize_filename(d.get("title", ""))
            ctx = d.get("context", "")[:40]
            f.write(f"- [[decisions/{slug}]] — {ctx} ({today})\n")
        # 经验条目
        for lsn in extracted.get("lessons", []):
            slug = normalize_filename(lsn.get("title", ""))
            takeaway = lsn.get("key_takeaway", "")[:40]
            f.write(f"- [[lessons/{slug}]] — {takeaway} ({today})\n")


# ── 操作日志 ──


def _append_log(source_path: str, created_pages: list[str], pages_dir: str):
    """在 log.md 中追加操作记录。"""
    log_path = os.path.join(pages_dir, "log.md")
    if not file_exists(log_path):
        return
    from datetime import datetime

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rel_path = os.path.relpath(source_path, os.path.dirname(pages_dir))
    page_list = ", ".join(created_pages)
    line = f"[{now}] INGEST {rel_path} → {page_list}\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


# ── 工具 ──


def _safe_write(path: str, content: str):
    """目录检查和写入。"""
    ensure_dir(os.path.dirname(path))
    write_md(path, content)
