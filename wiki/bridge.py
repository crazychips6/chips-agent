"""Wiki ↔ Agent 桥梁 — 知识驱动的上下文注入 + 会话自动捕获

两大能力：
1. 上下文注入：用户发消息时自动查找相关 wiki 页面，注入 system prompt
2. 会话捕获：对话结束后自动提取决策/经验/概念写入 wiki

设计原则：
- 轻量启发式 + LLM 兜底（不做 Embedding 等重量级依赖）
- 采样捕获而非每轮捕获（turn_count % 3），不浪费 token
- 只捕获工具调用的深度对话，忽略简单问答
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from typing import Any

from wiki.config import get_wiki_dir, get_wiki_pages_dir
from wiki.files import read_md, file_exists

logger = logging.getLogger("chips.wiki.bridge")

# ── 关键词提取 ──

_CHINESE_RE = re.compile(r"[一-鿿]{2,}")
_ENGLISH_RE = re.compile(r"[a-zA-Z]{3,}")


def _extract_keywords(text: str) -> set[str]:
    """提取文本中的关键词（中文 2 字以上 + 英文 3 字母以上）。"""
    chinese = {w.lower() for w in _CHINESE_RE.findall(text)}
    english = {w.lower() for w in _ENGLISH_RE.findall(text)}
    return chinese | english


# ── 上下文注入 ──


def match_relevant_pages(
    query: str, wiki_dir: str | None = None, top_k: int = 3
) -> list[dict[str, Any]]:
    """基于关键词匹配找到相关 wiki 页面。

    读取 index.md，将用户提问关键词与条目描述做交集匹配。
    返回 [{path, description, summary, score}, ...] 按相关度排序。
    """
    base = wiki_dir or get_wiki_dir()
    pages_dir = get_wiki_pages_dir(base)
    index_path = os.path.join(pages_dir, "index.md")
    if not file_exists(index_path):
        return []

    query_keywords = _extract_keywords(query)
    if not query_keywords:
        return []

    index_content = read_md(index_path)
    scored: list[tuple[str, str, int]] = []

    for line in index_content.split("\n"):
        line = line.strip()
        if not line.startswith("- [["):
            continue
        m = re.match(r"- \[\[([^\]]+)\]\] — (.+) \(\d{4}-\d{2}-\d{2}\)", line)
        if not m:
            continue
        path = m.group(1)
        desc = m.group(2)
        page_name = path.split("/")[-1].replace("-", " ").replace("_", " ")
        desc_kw = _extract_keywords(f"{desc} {page_name}")
        overlap = len(query_keywords & desc_kw)
        if overlap > 0:
            scored.append((path, desc, overlap))

    scored.sort(key=lambda x: x[2], reverse=True)
    top = scored[:top_k]
    if not top:
        return []

    result = []
    for path, desc, score in top:
        page_path = os.path.join(pages_dir, f"{path}.md")
        if file_exists(page_path):
            content = read_md(page_path)
            # 取头部关键内容
            summary = _extract_page_summary(content)
            result.append({
                "path": path,
                "description": desc,
                "summary": summary,
                "score": score,
            })

    return result


def _extract_page_summary(content: str, max_chars: int = 600) -> str:
    """提取页面摘要：标题 + 定义 + 前几个要点。"""
    lines = content.split("\n")
    important = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            important.append(stripped)
        elif stripped.startswith("## 定义") or stripped.startswith("## 一句话摘要"):
            important.append(stripped)
        elif stripped.startswith("- ") and len(important) < 8:
            important.append(stripped)
        elif stripped.startswith("**类型**"):
            important.append(stripped)
        current = "\n".join(important)
        if len(current) > max_chars:
            break
    result = "\n".join(important)
    return result[:max_chars]


def inject_wiki_context(query: str, wiki_dir: str | None = None) -> str:
    """生成 <wiki-context> XML 块供 system prompt 注入。

    空字符串 = 无相关内容，调用方无需注入。
    """
    pages = match_relevant_pages(query, wiki_dir, top_k=3)
    if not pages:
        return ""

    blocks = []
    for p in pages:
        title_line = p["summary"].split("\n")[0] if p["summary"] else p["path"]
        title = title_line.lstrip("# ")
        blocks.append(
            f'<page path="{p["path"]}">\n'
            f"  <title>{title}</title>\n"
            f"  <summary>{p['description']}</summary>\n"
            f"  <content>\n{p['summary']}\n  </content>\n"
            f"</page>"
        )

    result = (
        "\n<wiki-context>\n"
        "你的个人知识库中包含以下与当前问题相关的内容：\n"
        + "\n".join(blocks)
        + "\n</wiki-context>"
    )
    return result


# ── 会话自动捕获 ──

# 信号词：最终回复含这些词 → 值得尝试捕获
_CAPTURE_SIGNALS = {
    "决定", "选择", "方案", "发现", "教训", "经验", "注意",
    "decided", "chose", "found", "lesson", "learned", "important",
    "关键", "核心", "本质", "原因", "因为", "所以", "推荐",
}

# 反信号：如果命中这些简单意图 → 跳过捕获
_SKIP_INTENTS = {"greeting", "simple_qa", "other"}


def _should_capture(messages: list[dict], intent: str = "") -> bool:
    """启发式检查：会话中是否有值得 wiki 记录的内容。"""
    if intent in _SKIP_INTENTS:
        return False

    # 必须有工具调用
    has_tools = any(
        msg.get("role") == "assistant" and msg.get("tool_calls")
        for msg in messages
    )
    if not has_tools:
        return False

    # 最终回复含信号词
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            text = str(msg.get("content", ""))
            if any(w in text for w in _CAPTURE_SIGNALS):
                return True
            break

    return False


def _extract_conversation_context(
    messages: list[dict], max_turns: int = 5
) -> str:
    """提取最近几轮对话上下文（用户 + 助手 + 工具调用）。"""
    parts: list[str] = []
    turn_count = 0

    for msg in reversed(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")

        if role == "user":
            text = str(content)[:500] if content else ""
            if text:
                parts.append(f"用户: {text}")
                turn_count += 1
        elif role == "assistant":
            text = str(content)[:500] if content else ""
            if text:
                parts.append(f"助手: {text}")
            if tool_calls:
                names = [tc["function"]["name"] for tc in tool_calls]
                parts.append(f"工具调用: {', '.join(names)}")

        if turn_count >= max_turns:
            break

    return "\n".join(reversed(parts))


_CAPTURE_PROMPT = """你是一个知识提取器。从对话中提取值得记录到个人知识库的信息。

返回 JSON（只输出纯 JSON，不要 markdown 代码块）：
{
  "has_content": false,
  "decisions": [],
  "lessons": [],
  "concepts": []
}

字段说明：
  has_content: 是否有值得记录的内容
  decisions: [{"title": "决策标题", "context": "背景", "rationale": "理由"}]
  lessons: [{"title": "经验标题", "problem": "问题", "solution": "解决", "key_takeaway": "核心收获"}]
  concepts: [{"name": "概念名", "definition": "一句话定义"}]

规则：
1. has_content=false 如果没有明确可记录的内容，宁可漏过不要误报
2. 只提取明确的决策（含 rationale）、可复用的经验教训、或新的概念
3. 标题控制在 15 字以内
4. 每个数组最多 3 条"""


def _call_llm_capture(context: str, gateway, model: str) -> dict | None:
    """调用 LLM 提取 wiki-worthy 内容。"""
    messages = [
        {"role": "system", "content": _CAPTURE_PROMPT},
        {
            "role": "user",
            "content": f"从以下对话中提取可记录的信息：\n\n{context[:4000]}",
        },
    ]

    for attempt in range(2):
        try:
            result = gateway.chat(
                messages=messages, model=model, max_tokens=2048
            )
            raw = result.content.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception as e:
            logger.debug("wiki_capture_retry attempt=%d error=%s", attempt + 1, e)
    return None


def _write_captured(extracted: dict, wiki_dir: str) -> dict[str, Any]:
    """将提取的内容写入 wiki 文件。"""
    today = date.today().isoformat()
    pages_dir = get_wiki_pages_dir(wiki_dir)
    captured: dict[str, int] = {"decisions": 0, "lessons": 0, "concepts": 0}
    new_entries: list[str] = []

    for d in extracted.get("decisions", []):
        from wiki.files import normalize_filename

        slug = normalize_filename(d.get("title", ""))
        if not slug:
            continue
        path = os.path.join(pages_dir, "decisions", f"{slug}.md")
        if file_exists(path):
            continue
        from wiki.ingest import _create_decision_page

        _create_decision_page(path, d, "auto-capture")
        captured["decisions"] += 1
        new_entries.append(f"decisions/{slug}")
        logger.info("wiki_auto_capture type=decision slug=%s", slug)

    for lsn in extracted.get("lessons", []):
        from wiki.files import normalize_filename

        slug = normalize_filename(lsn.get("title", ""))
        if not slug:
            continue
        path = os.path.join(pages_dir, "lessons", f"{slug}.md")
        if file_exists(path):
            continue
        from wiki.ingest import _create_lesson_page

        _create_lesson_page(path, lsn, "auto-capture")
        captured["lessons"] += 1
        new_entries.append(f"lessons/{slug}")
        logger.info("wiki_auto_capture type=lesson slug=%s", slug)

    for c in extracted.get("concepts", []):
        from wiki.files import normalize_filename

        slug = normalize_filename(c.get("name", ""))
        if not slug:
            continue
        path = os.path.join(pages_dir, "concepts", f"{slug}.md")
        if file_exists(path):
            continue
        from wiki.ingest import _create_concept_page

        concept_data = {
            "name": c.get("name", ""),
            "slug": slug,
            "definition": c.get("definition", ""),
            "details": c.get("definition", ""),
            "source_context": "来自对话自动捕获",
        }
        _create_concept_page(path, concept_data, "auto-capture")
        captured["concepts"] += 1
        new_entries.append(f"concepts/{slug}")
        logger.info("wiki_auto_capture type=concept slug=%s", slug)

    # 更新 index.md
    if new_entries:
        index_path = os.path.join(pages_dir, "index.md")
        with open(index_path, "a", encoding="utf-8") as f:
            for entry in new_entries:
                f.write(f"- [[{entry}]] — 对话自动捕获 ({today})\n")
        # 更新 log.md
        log_path = os.path.join(pages_dir, "log.md")
        from datetime import datetime

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry_list = ", ".join(new_entries)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{now}] CAPTURE {entry_list}\n")

    total = sum(captured.values())
    return {"captured": total > 0, "count": total, **captured}


def auto_capture_from_session(
    messages: list[dict],
    gateway,
    model: str,
    wiki_dir: str | None = None,
    intent: str = "",
) -> dict[str, Any]:
    """从会话中自动提取并写入 wiki 条目。

    三步：启发式检查 → LLM 提取 → 写入文件
    """
    base = wiki_dir or get_wiki_dir()

    if not _should_capture(messages, intent):
        return {"captured": False, "reason": "no_signals"}

    context = _extract_conversation_context(messages)
    extracted = _call_llm_capture(context, gateway, model)
    if not extracted or not extracted.get("has_content"):
        return {"captured": False, "reason": "no_content"}

    result = _write_captured(extracted, base)
    if result.get("captured"):
        logger.info(
            "wiki_auto_captured decisions=%d lessons=%d concepts=%d",
            result.get("decisions", 0),
            result.get("lessons", 0),
            result.get("concepts", 0),
        )
    return result
