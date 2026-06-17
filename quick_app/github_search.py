"""GitHub 项目搜索 — 用 GitHub REST API 搜索相关项目

搜索查询由 LLM 使用 GitHub qualifiers 构造（如 in:name,description language:Python stars:>50），
不再使用硬编码中→英映射表。"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable

logger = logging.getLogger("chips.quick_app.github")

_GITHUB_API = "https://api.github.com"
_SEARCH_REPO = "/search/repositories"

# LLM 构建 GitHub 查询的 prompt
_QUERY_PROMPT = """你是一个 GitHub 搜索专家。根据用户描述，生成一个精确的 GitHub 仓库搜索查询。

要求：
- 使用英文关键词（如 recipe, video converter, pdf tool）
- 使用 GitHub 搜索限定符：
  - in:name,description — 关键词出现在仓库名或描述中
  - language:Python — 只搜 Python 项目
  - stars:>50 — 过滤低质量项目（如果场景需要大量结果可降低或去掉）
  - pushed:>2024-01-01 — 只看近期活跃项目
  - extension:py path: — 搜索代码内容（当需求很具体时用）
  - OR 连接同义词 — recipe OR cookbook OR "meal planner"
- 不需要的项目加 -排除词
- 返回纯查询字符串，不要多余文字

用户描述：{description}
展开关键词：{keywords}

示例输出：
recipe OR cookbook OR "meal planner" in:name,description language:Python stars:>50 pushed:>2024-01-01

注意：
- 如果描述是"视频转GIF"，查询应包含 video, gif, converter 等词
- 如果描述是"用Flask写的食谱API"，应包含 flask, recipe, api, path:requirements.txt 等
- 如果描述很模糊（如"工具"），返回空字符串，不搜索"""


def _get_token() -> str:
    return os.getenv("GITHUB_TOKEN", "")


def build_github_query(
    description: str,
    keywords: list[str] | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> str:
    """用 LLM 构建 GitHub 搜索查询（含 qualifiers）。

    LLM 不可用时退回到简单关键词查询。

    Args:
        description: 用户原始描述。
        keywords: LLM 展开后的关键词列表（可选）。
        llm_call: LLM 调用函数，如 gateway.chat 的包装。

    Returns:
        GitHub 搜索查询字符串，空字符串表示不搜索。
    """
    kw_str = ", ".join(keywords[:6]) if keywords else "(无)"
    prompt = _QUERY_PROMPT.format(description=description, keywords=kw_str)

    if llm_call:
        try:
            query = llm_call(prompt).strip()
            # 清理可能的 markdown 代码块
            if query.startswith("```"):
                lines = query.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                query = "\n".join(lines).strip()
            if query:
                from quick_app.trace_log import write as tw
                tw("GITHUB", "LLM 构造查询",
                    {"description": description[:60], "keywords": keywords, "generated_query": query})
                logger.info("github_query_llm query=%s", query[:120])
                return query
        except Exception as e:
            logger.warning("github_query_llm_failed: %s", e)

    # LLM 不可用时的降级：用关键词构简单查询
    all_kw = keywords or []
    if not all_kw:
        # 从描述中提取英文词
        import re
        all_kw = [w.lower() for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_.-]*", description) if len(w) > 2]
    if all_kw:
        query = " ".join(all_kw[:3]) + " in:name,description language:Python"
        logger.info("github_query_fallback query=%s", query)
        return query

    return ""


def search_github(
    description: str,
    limit: int = 3,
    llm_call: Callable[[str], str] | None = None,
    keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """搜索 GitHub 仓库。

    Args:
        description: 用户描述。
        limit: 最多返回结果数。
        llm_call: LLM 调用函数（用于构建精确查询）。
        keywords: LLM 展开后的关键词。

    Returns:
        列表，每项含 name/description/stars/language/url/reason 等字段。
    """
    query = build_github_query(description, keywords=keywords, llm_call=llm_call)
    if not query:
        return []

    token = _get_token()
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "chips-agent/1.0",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    url = f"{_GITHUB_API}{_SEARCH_REPO}?q={urllib.request.quote(query)}&sort=stars&order=desc&per_page={limit * 2}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        from quick_app.trace_log import write as tw
        total = data.get("total_count", 0)
        items_abbrev = [{"name": i.get("full_name"), "stars": i.get("stargazers_count"), "desc": (i.get("description") or "")[:60]} for i in data.get("items", [])[:5]]
        tw("GITHUB", f"API 返回 {total} 条", {"query": query[:80], "total": total, "top_5": items_abbrev})
    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.warning("github_search rate_limited or token invalid")
        else:
            logger.warning("github_search http_error code=%d", e.code)
        return []
    except Exception as e:
        logger.warning("github_search failed: %s", e)
        return []

    # 从查询中提取搜索词用于相关性过滤
    search_terms = query.lower().split()

    results: list[dict[str, Any]] = []
    for item in data.get("items", []):
        name = item.get("full_name", "")
        desc = item.get("description") or ""
        stars = item.get("stargazers_count", 0)
        lang = item.get("language") or ""
        url = item.get("html_url", "")
        topics = item.get("topics", [])
        updated = item.get("updated_at", "")
        has_readme = item.get("has_readme", True)
        has_cli = _has_cli_hint(desc, topics)

        if not desc and not topics:
            continue

        reason_parts = []
        if has_cli:
            reason_parts.append("有 CLI 入口")
        if stars > 100:
            reason_parts.append(f"{stars} stars")
        if lang:
            reason_parts.append(f"语言: {lang}")

        # 相关性过滤：名称或描述包含至少一个搜索词
        desc_lower = (name + " " + desc + " " + " ".join(topics)).lower()
        is_relevant = any(term.strip('"').lower() in desc_lower for term in search_terms if term not in ("in:name,description", "language:python", "in:name", "in:description", "stars:>50", "stars:>100"))
        if not is_relevant:
            continue

        results.append({
            "source_type": "github",
            "source_name": name,
            "description": desc or f"GitHub 项目 {name}",
            "stars": stars,
            "language": lang,
            "url": url,
            "topics": topics,
            "updated_at": updated,
            "reason": "；".join(reason_parts) if reason_parts else "GitHub 项目",
            "score": _calc_score(stars, has_cli, has_readme, desc),
        })

    results.sort(key=lambda r: -r["score"])
    return results[:limit]


def _has_cli_hint(description: str, topics: list[str]) -> bool:
    cli_keywords = ["cli", "command-line", "terminal", "命令行"]
    desc_lower = description.lower()
    for kw in cli_keywords:
        if kw in desc_lower:
            return True
    for t in topics:
        if t.lower() in cli_keywords:
            return True
    return False


def _calc_score(stars: int, has_cli: bool, has_readme: bool, desc: str) -> float:
    score = 0.0
    score += min(stars / 1000, 5.0)
    if has_cli:
        score += 2.0
    if has_readme:
        score += 1.0
    if desc:
        score += 1.0
    return score
