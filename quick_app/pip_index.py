"""pip 包索引 — 搜索 PyPI 获取包信息

当 known-good pip 包不匹配时，通过 PyPI JSON API 实时搜索。
结果缓存到本地文件以减少重复请求。"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("chips.quick_app.pip_index")

_PYPI_API = "https://pypi.org/pypi"
_CACHE_DIR = Path(".chips") / "pip_cache"
_CACHE_TTL = 86400.0  # 24 小时


def _ensure_cache():
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(name: str) -> Path:
    return _CACHE_DIR / f"{name}.json"


def _read_cache(name: str) -> dict | None:
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("_cached_at", 0) < _CACHE_TTL:
            return data
    except Exception:
        pass
    return None


def _write_cache(name: str, data: dict):
    _ensure_cache()
    data["_cached_at"] = time.time()
    try:
        _cache_path(name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def search_pypi(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """在 PyPI 搜索包。

    直接尝试已知包名 + 关键词派生包名，用 JSON API 获取详情。
    PyPI 已废弃 XML-RPC search，所以不再使用。

    Args:
        query: 搜索关键词。
        limit: 最多返回结果数。

    Returns:
        列表，每项含 name/description/version/summary/url。
    """
    query = query.strip().lower()
    if not query:
        return []

    # 从关键词生成候选包名
    candidates = _generate_candidates(query)
    results = []

    for pkg_name in candidates:
        if len(results) >= limit:
            break
        info = _get_package_info(pkg_name)
        if info:
            results.append({
                "source_type": "pip_package",
                "source_name": pkg_name,
                "version": info.get("version", ""),
                "description": info.get("summary", ""),
                "url": f"https://pypi.org/project/{pkg_name}/",
                "author": info.get("author", ""),
                "reason": f"PyPI 包 {pkg_name}",
            })

    return results


def _generate_candidates(query: str) -> list[str]:
    """从描述生成候选包名列表。"""
    import re

    # 常见中→英映射 + 已知常用包
    KNOWN_MAP = {
        "http": "requests",
        "api": "requests",
        "网页": "requests",
        "html": "beautifulsoup4",
        "解析": "beautifulsoup4",
        "爬虫": "scrapy",
        "图片": "Pillow",
        "图像": "Pillow",
        "excel": "openpyxl",
        "xlsx": "openpyxl",
        "报表": "openpyxl",
        "数据": "pandas",
        "分析": "pandas",
        "csv": "pandas",
        "pdf": "pypdf",
        "表格": "camelot-py",
        "ocr": "pytesseract",
        "识别": "pytesseract",
        "邮件": "smtplib",
        "翻译": "googletrans",
        "视频": "yt-dlp",
        "下载": "yt-dlp",
        "压缩": "zipfile",
        "加密": "cryptography",
        "数据库": "sqlalchemy",
        "聊天": "openai",
    }

    candidates = []
    query_lower = query.lower()

    # 1. 直接用完整 query 尝试
    candidates.append(query_lower.replace(" ", "-"))
    candidates.append(query_lower.replace(" ", "_"))
    candidates.append(query_lower.replace(" ", ""))

    # 2. 取第一个有意义的词
    words = re.findall(r"[a-zA-Z一-鿿]+", query_lower)
    if words:
        first = words[0].lower()
        candidates.append(first)
        candidates.append(f"py-{first}")
        candidates.append(f"python-{first}")

    # 3. 已知映射
    for zh, pkg in KNOWN_MAP.items():
        if zh in query_lower:
            candidates.append(pkg)

    # 去重
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    return unique


def _try_direct_fetch(package_name: str) -> list[dict[str, Any]]:
    """尝试直接获取指定包名的信息。"""
    info = _get_package_info(package_name)
    if not info:
        return []

    return [{
        "source_type": "pip_package",
        "source_name": package_name,
        "version": info.get("version", ""),
        "description": info.get("summary", ""),
        "url": f"https://pypi.org/project/{package_name}/",
        "author": info.get("author", ""),
        "reason": f"PyPI 包 {package_name}",
    }]


def _get_package_info(package_name: str) -> dict[str, Any]:
    """获取单个包的信息（带缓存）。"""
    if not package_name:
        return {}

    cached = _read_cache(package_name)
    if cached:
        return cached

    try:
        url = f"{_PYPI_API}/{urllib.request.quote(package_name)}/json"
        req = urllib.request.Request(url, headers={"User-Agent": "chips-agent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.warning("pypi_info http_error pkg=%s code=%d", package_name, e.code)
        return {}
    except Exception as e:
        logger.warning("pypi_info failed pkg=%s: %s", package_name, e)
        return {}

    info = data.get("info", {})
    result = {
        "version": info.get("version", ""),
        "summary": (info.get("summary") or "").strip(),
        "author": info.get("author", ""),
        "home_page": info.get("home_page", ""),
        "project_urls": info.get("project_urls", {}),
    }

    _write_cache(package_name, result)
    return result


def get_search_suggestions(keywords: list[str]) -> list[str]:
    """从关键词生成 PyPI 搜索建议。

    基于常见命名模式（prefix/suffix/infix）给建议。
    """
    suggestions = set()
    for kw in keywords:
        # 直接搜索关键词
        suggestions.add(kw)
        # py- 前缀模式
        suggestions.add(f"py-{kw}")
        # python- 前缀模式
        suggestions.add(f"python-{kw}")
        # 常见工具名模式
        if kw in ("http", "api"):
            suggestions.add("requests")
        elif kw in ("html", "parse"):
            suggestions.add("beautifulsoup4")
        elif kw in ("excel", "xlsx"):
            suggestions.add("openpyxl")
            suggestions.add("xlrd")

    return list(suggestions)
