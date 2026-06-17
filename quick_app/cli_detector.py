"""本地 CLI 探测 — 扫描 PATH 发现已安装的工具

在启动时更新 known-good 索引中 CLI 工具的可用性标记。
在创建快应用时提示用户哪些工具已安装/未安装。"""

from __future__ import annotations

import logging
import shutil
import time
from typing import Any

logger = logging.getLogger("chips.quick_app.cli_detector")

# 缓存探测结果，避免频繁扫描 PATH
_cache: dict[str, bool] = {}
_cache_time = 0.0
_CACHE_TTL = 300.0  # 5 分钟


def detect_tool(name: str) -> bool:
    """检查指定 CLI 工具是否在 PATH 中。

    结果会缓存 5 分钟。

    Args:
        name: 工具名（如 ffmpeg, pandoc, yt-dlp）。

    Returns:
        True 表示工具已安装且可用。
    """
    global _cache_time
    now = time.time()

    if name in _cache and (now - _cache_time) < _CACHE_TTL:
        return _cache[name]

    found = shutil.which(name) is not None
    _cache[name] = found
    _cache_time = now
    return found


def scan_installed(tool_names: list[str]) -> dict[str, bool]:
    """批量扫描工具安装状态。

    Args:
        tool_names: 要检查的工具名列表。

    Returns:
        {工具名: 是否已安装} 字典。
    """
    results = {}
    for name in tool_names:
        results[name] = detect_tool(name)
    return results


def get_installed_cli_tools(tool_names: list[str]) -> list[str]:
    """返回已安装的工具名列表。"""
    return [name for name, installed in scan_installed(tool_names).items() if installed]


def get_missing_cli_tools(tool_names: list[str]) -> list[str]:
    """返回未安装的工具名列表。"""
    return [name for name, installed in scan_installed(tool_names).items() if not installed]


def enhance_known_good_with_availability(index_entries: list[Any]) -> list[dict]:
    """给 known-good CLI 条目添加安装状态标记。

    用于在 draft 卡片中显示哪些工具已安装、哪些需安装。

    Args:
        index_entries: KnownGoodEntry 列表。

    Returns:
        带有 install_status 标记的字典列表。
    """
    from quick_app.models import SourceType

    results = []
    for entry in index_entries:
        d = {
            "name": entry.name,
            "description": entry.description,
            "source_type": entry.source_type.value,
            "source_name": entry.name,
            "params": list(entry.params_template),
            "install_hint": entry.install_hint,
            "scene_tags": entry.scene_tags,
        }

        if entry.source_type == SourceType.CLI_TOOL:
            installed = detect_tool(entry.name)
            d["installed"] = installed
            d["install_status"] = "已安装" if installed else "未安装"
            # 对于复合名（如 "系统压缩工具"），检查第一关键词
            if not installed and entry.keywords:
                for kw in entry.keywords[:3]:
                    if shutil.which(kw):
                        d["installed"] = True
                        d["install_status"] = f"可通过 {kw} 使用"
                        break

        results.append(d)

    return results
