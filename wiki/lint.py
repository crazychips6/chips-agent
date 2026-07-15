"""Lint 健康检查 — 断链 / 孤儿页 / 索引一致 / 过期 / 矛盾

入口：
  run_lint(gateway, model, wiki_dir=None, skip_contradictions=False, auto_fix=False)
  返回 Markdown 格式的 lint 报告
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime
from typing import Any

from wiki.config import get_wiki_pages_dir, get_stale_days, get_critical_days
from wiki.files import (
    read_md,
    file_exists,
    list_md_files,
    extract_wikilinks,
    parse_frontmatter,
)
from wiki.prompts import DETECT_CONTRADICTIONS

logger = logging.getLogger("chips.wiki.lint")

# 正则提取 frontmatter 中的更新日期
DATE_RE = re.compile(r"\*\*更新\*\*:\s*(\d{4}-\d{2}-\d{2})")


def run_lint(
    gateway=None,
    model: str | None = None,
    wiki_dir: str | None = None,
    skip_contradictions: bool = False,
    auto_fix: bool = False,
) -> str:
    """执行完整 Lint 检查。返回 Markdown 格式的报告。"""
    base_dir = wiki_dir or os.path.expanduser("~/.chips/wiki")
    pages_dir = get_wiki_pages_dir(base_dir)

    if not os.path.isdir(pages_dir):
        return "## Lint 报告\n\nWiki 目录不存在，请先执行 ingest。"

    today = date.today().isoformat()
    all_pages = list_md_files(pages_dir)

    # 排除 index.md 和 log.md
    wiki_pages = [p for p in all_pages if not p.endswith(("index.md", "log.md"))]

    broken_links: list[dict] = []
    orphan_pages: list[str] = []
    index_missing: list[str] = []
    stale_pages: list[dict] = []
    contradictions: list[dict] = []
    fixable_count = 0

    # ── 1. 断链检测 ──
    for page_path in wiki_pages:
        content = read_md(page_path)
        links = extract_wikilinks(content)
        for link in links:
            target = _resolve_link_target(link, pages_dir)
            if not file_exists(target):
                rel_page = os.path.relpath(page_path, pages_dir)
                broken_links.append({
                    "source_page": rel_page,
                    "broken_target": link,
                    "suggested_path": link,
                })

    # ── 2. 孤儿页面检测 ──
    inbound_counts: dict[str, int] = {}
    for page_path in wiki_pages:
        content = read_md(page_path)
        links = extract_wikilinks(content)
        for link in links:
            # 规范化链接目标
            target = _normalize_link_target(link)
            inbound_counts[target] = inbound_counts.get(target, 0) + 1

    for page_path in wiki_pages:
        rel = os.path.relpath(page_path, pages_dir)
        rel_no_ext = os.path.splitext(rel)[0]
        # index.md 和 log.md 算入链
        if inbound_counts.get(rel_no_ext, 0) == 0:
            orphan_pages.append(rel)

    # ── 3. 索引一致性 ──
    index_path = os.path.join(pages_dir, "index.md")
    if file_exists(index_path):
        index_content = read_md(index_path)
        index_entries = set(extract_wikilinks(index_content))
    else:
        index_entries = set()

    # wiki/ 中存在但 index.md 中缺失的页面
    actual_pages = set()
    for p in wiki_pages:
        rel = os.path.relpath(p, pages_dir)
        rel_no_ext = os.path.splitext(rel)[0]
        actual_pages.add(rel_no_ext)

    # index.md 中有但实际文件不存在的
    for entry in index_entries:
        entry_path = os.path.join(pages_dir, f"{entry}.md")
        if not file_exists(entry_path):
            index_missing.append(entry)

    # 实际存在但 index.md 没有的
    for ap in actual_pages:
        if ap not in index_entries:
            # 可能是 index.md 格式不一致，检查是否包含在 index.md 文本中
            if file_exists(index_path):
                index_content = read_md(index_path)
                if ap not in index_content:
                    index_missing.append(f"{ap}（不在 index.md 中）")

    # ── 4. 过期检测 ──
    stale_days = get_stale_days()
    critical_days = get_critical_days()
    for page_path in wiki_pages:
        content = read_md(page_path)
        match = DATE_RE.search(content)
        if match:
            try:
                updated = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                days_since = (date.today() - updated).days
                if days_since > critical_days:
                    rel = os.path.relpath(page_path, pages_dir)
                    stale_pages.append({
                        "page": rel,
                        "days_since_update": days_since,
                        "severity": "critical",
                    })
                elif days_since > stale_days:
                    rel = os.path.relpath(page_path, pages_dir)
                    stale_pages.append({
                        "page": rel,
                        "days_since_update": days_since,
                        "severity": "warn",
                    })
            except (ValueError, IndexError):
                pass

    # ── 5. 矛盾检测（LLM 调用，可选） ──
    if not skip_contradictions and gateway and model:
        # 找成对的概念页面做矛盾检测
        concept_pages = [p for p in wiki_pages if "/concepts/" in p]
        for i in range(len(concept_pages)):
            for j in range(i + 1, len(concept_pages)):
                # 只检查相关概念（名称有关联的）
                pa = concept_pages[i]
                pb = concept_pages[j]
                name_a = os.path.splitext(os.path.basename(pa))[0]
                name_b = os.path.splitext(os.path.basename(pb))[0]
                # 简单启发：只检查文件名包含共同关键词的页面对
                common = _common_keywords(name_a, name_b)
                if not common:
                    continue
                # 最多检测 5 对
                if len(contradictions) >= 5:
                    break
                result = _check_contradiction(pa, pb, gateway, model, pages_dir)
                if result:
                    contradictions.append(result)
            if len(contradictions) >= 5:
                break

    # ── 生成报告 ──
    report_parts = [f"## Lint 报告 — {today}\n"]

    if broken_links:
        report_parts.append(f"### 🔴 断链 ({len(broken_links)})")
        for bl in broken_links[:10]:
            report_parts.append(f"- `[[{bl['broken_target']}]]` 在 `{bl['source_page']}` → 目标不存在")
            fixable_count += 1
        if len(broken_links) > 10:
            report_parts.append(f"- ...还有 {len(broken_links) - 10} 个断链")
        report_parts.append("")

    if orphan_pages:
        report_parts.append(f"### 🟡 孤儿页面 ({len(orphan_pages)})")
        for op in orphan_pages[:10]:
            # 获取最后更新日期
            path = os.path.join(pages_dir, op)
            content = read_md(path)
            match = DATE_RE.search(content)
            days = ""
            if match:
                days = f"（{_days_since_str(match.group(1))}）"
            report_parts.append(f"- `{op}` — 无任何其他页面引用{days}")
        if len(orphan_pages) > 10:
            report_parts.append(f"- ...还有 {len(orphan_pages) - 10} 个孤儿页面")
        report_parts.append("")

    if index_missing:
        report_parts.append(f"### 🟡 索引不一致 ({len(index_missing)})")
        for im in index_missing[:10]:
            report_parts.append(f"- `{im}`")
        if len(index_missing) > 10:
            report_parts.append(f"- ...还有 {len(index_missing) - 10} 个")
        report_parts.append("")

    if stale_pages:
        critical_count = sum(1 for s in stale_pages if s["severity"] == "critical")
        report_parts.append(f"### 🟠 过期页面 ({len(stale_pages)})")
        critical_first = sorted(stale_pages, key=lambda x: x["days_since_update"], reverse=True)
        for sp in critical_first[:10]:
            icon = "⚠️ 严重过期" if sp["severity"] == "critical" else "过期"
            report_parts.append(f"- `{sp['page']}` — {sp['days_since_update']} 天未更新 {icon}")
        if len(stale_pages) > 10:
            report_parts.append(f"- ...还有 {len(stale_pages) - 10} 个")
        report_parts.append("")

    if contradictions:
        report_parts.append(f"### 🔵 可能矛盾 ({len(contradictions)})")
        for c in contradictions[:5]:
            sev_icon = {"high": "🔴", "medium": "🟡", "low": "🔵"}.get(c.get("severity", "low"), "🔵")
            report_parts.append(f"{sev_icon} `{c['page_a']}` 说 \"{c['claim_a'][:60]}\"")
            report_parts.append(f"   `{c['page_b']}` 说 \"{c['claim_b'][:60]}\"")
        report_parts.append("")

    # 建议
    suggestions: list[str] = []
    if broken_links:
        suggestions.append(f"创建或修复 {len(broken_links)} 个断链页面")
    if orphan_pages:
        suggestions.append(f"检查 {len(orphan_pages)} 个孤儿页面是否仍需保留，或添加引用")
    if index_missing:
        suggestions.append(f"更新 index.md 以匹配实际文件")
    if stale_pages:
        critical_count = sum(1 for s in stale_pages if s["severity"] == "critical")
        if critical_count:
            suggestions.append(f"优先审查 {critical_count} 个严重过期页面")
    if contradictions:
        suggestions.append("手动确认矛盾项的统一表述")

    if suggestions:
        report_parts.append("### 💡 建议操作")
        for i, s in enumerate(suggestions, 1):
            report_parts.append(f"{i}. {s}")
        report_parts.append("")

    if not any([broken_links, orphan_pages, index_missing, stale_pages, contradictions]):
        report_parts.append("✅ 未发现任何问题，知识库状态健康。\n")

    return "\n".join(report_parts)


# ── 辅助函数 ──


def _resolve_link_target(link_target: str, pages_dir: str) -> str:
    """将 [[wikilink]] 转换为实际文件路径。"""
    if "/" in link_target:
        # 格式如 concepts/self-attention
        return os.path.join(pages_dir, f"{link_target}.md")

    # 无前缀 → 在所有子目录中搜索
    for subdir in ("concepts", "entities", "sources", "decisions", "lessons", "answers"):
        candidate = os.path.join(pages_dir, subdir, f"{link_target}.md")
        if os.path.isfile(candidate):
            return candidate

    return os.path.join(pages_dir, "concepts", f"{link_target}.md")


def _normalize_link_target(link_target: str) -> str:
    """规范化链接目标，移除文件扩展名等。"""
    target = link_target.rstrip(".md")
    return target


def _days_since_str(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        days = (date.today() - d).days
        return f"{days} 天前更新"
    except (ValueError, IndexError):
        return ""


def _common_keywords(a: str, b: str) -> bool:
    """检查两个文件名是否有共同关键词。"""
    a_words = set(re.split(r"[-_]", a.lower()))
    b_words = set(re.split(r"[-_]", b.lower()))
    common = a_words & b_words
    # 过滤太短的词
    common = {w for w in common if len(w) > 2}
    return bool(common)


def _check_contradiction(
    page_a: str, page_b: str, gateway, model: str, pages_dir: str,
) -> dict | None:
    """调用 LLM 检查两页面间是否有矛盾。"""
    content_a = read_md(page_a)
    content_b = read_md(page_b)
    rel_a = os.path.relpath(page_a, pages_dir)
    rel_b = os.path.relpath(page_b, pages_dir)

    prompt = DETECT_CONTRADICTIONS.format(
        page_a=rel_a,
        page_b=rel_b,
        content_a=content_a[:2000],
        content_b=content_b[:2000],
    )

    messages = [
        {"role": "system", "content": "你是一个矛盾检测员。只输出 JSON。"},
        {"role": "user", "content": prompt},
    ]

    try:
        result = gateway.chat(messages=messages, model=model, max_tokens=512)
        raw = result.content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
        parsed = json.loads(raw)
        if parsed.get("has_contradiction"):
            return {
                "page_a": rel_a,
                "page_b": rel_b,
                "claim_a": parsed.get("claim_a", ""),
                "claim_b": parsed.get("claim_b", ""),
                "severity": parsed.get("severity", "low"),
            }
    except Exception as e:
        logger.debug("wiki_contradiction_check_skipped: %s", e)

    return None
