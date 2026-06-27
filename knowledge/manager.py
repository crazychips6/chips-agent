"""KnowledgeManager — 经验知识管理

知识是 agent 从 trace 中学习到的最优路径技巧，和记忆（事实）不同：
  - 记忆存事实（FTS5），由主 LLM 写入
  - 知识存技巧（JSON + git），由小模型 trace 分析 + 人工审核写入

置信度等级：
  observed (0) → candidate (1) → recommended (2) → authoritative (3)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("chips.knowledge")

_CONFIDENCE_ORDER = {
    "observed": 0,
    "candidate": 1,
    "recommended": 2,
    "authoritative": 3,
}


class KnowledgeManager:
    """经验知识管理器。

    Args:
        knowledge_dir: 知识库目录（默认 .chips/knowledge）
    """

    def __init__(self, knowledge_dir: str = ""):
        if not knowledge_dir:
            knowledge_dir = os.path.expanduser("~/.chips/knowledge")
        self._dir = Path(knowledge_dir)
        self._path = self._dir / "paths.json"
        self._entries: list[dict[str, Any]] = []
        self._load()

    # ── 读取 ──

    def _load(self):
        """从 paths.json 加载知识条目。"""
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
                self._entries = data.get("entries", [])
                logger.debug("knowledge_loaded entries=%d", len(self._entries))
            except Exception as exc:
                logger.warning("knowledge_load_failed: %s", exc)
                self._entries = []
        else:
            self._entries = []

    def match(self, user_message: str, max_results: int = 3) -> list[dict[str, Any]]:
        """匹配用户消息，返回最相关的知识条目。

        匹配规则：triggers 关键字命中 → 按置信度排序 → 取 top N。
        """
        matched = []
        for entry in self._entries:
            triggers = entry.get("triggers", [])
            if any(t in user_message for t in triggers):
                matched.append(entry)

        matched.sort(
            key=lambda e: _CONFIDENCE_ORDER.get(e.get("confidence", "observed"), 0),
            reverse=True,
        )
        return matched[:max_results]

    def format_knowledge(self, entries: list[dict[str, Any]]) -> str:
        """格式化为 prompt 注入文本。"""
        if not entries:
            return ""
        lines = ["# 经验知识"]
        for e in entries:
            icon = {
                "authoritative": "✅",
                "recommended": "📌",
                "candidate": "💡",
                "observed": "🔍",
            }
            c = e.get("confidence", "observed")
            lines.append(f"{icon.get(c, '•')} {e['task']} → {e['inject']}")
            avoid = e.get("avoid", "")
            if avoid:
                lines.append(f"   ⚠ {avoid}")
        return "\n".join(lines)

    # ── Staging（待审核知识） ──

    _STAGING_DIR = "staging"

    def save_to_staging(self, analysis: dict) -> str | None:
        """将小模型分析结果保存到 staging 区。返回 staging 文件 ID 或 None。"""
        optimal = analysis.get("optimal", "").strip()
        if not optimal:
            logger.info("staging_skip: empty optimal")
            return None

        entry = {
            "id": f"{_normalize_task(analysis.get('task', 'unknown'))}-{int(time.time())}",
            "task": analysis.get("task", "unknown"),
            "triggers": [],
            "inject": optimal,
            "avoid": "; ".join(analysis.get("waste", [])),
            "confidence": "candidate",
            "stats": {
                "success_count": 0,
                "fail_count": 0,
                "avg_tokens": analysis.get("tokens_saved_estimate", 0),
                "avg_steps": 0,
                "last_used": "",
            },
        }

        staging_dir = self._dir / self._STAGING_DIR
        staging_dir.mkdir(parents=True, exist_ok=True)
        fpath = staging_dir / f"{entry['id']}.json"
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
        logger.info("staging_saved id=%s path=%s", entry["id"], fpath)
        return entry["id"]

    def list_staging(self) -> list[dict]:
        """列出所有待审核知识条目。"""
        staging_dir = self._dir / self._STAGING_DIR
        if not staging_dir.exists():
            return []
        entries = []
        for fpath in sorted(staging_dir.iterdir()):
            if fpath.suffix != ".json":
                continue
            try:
                with open(fpath, encoding="utf-8") as f:
                    entries.append(json.load(f))
            except Exception as exc:
                logger.warning("staging_read_failed path=%s error=%s", fpath, exc)
        return entries

    def approve_staging(self, staging_id: str) -> bool:
        """批准一条待审核知识，合并到 paths.json。"""
        fpath = self._dir / self._STAGING_DIR / f"{staging_id}.json"
        if not fpath.exists():
            logger.warning("staging_not_found id=%s", staging_id)
            return False
        try:
            with open(fpath, encoding="utf-8") as f:
                entry = json.load(f)
        except Exception as exc:
            logger.warning("staging_read_error id=%s error=%s", staging_id, exc)
            return False

        self._merge_entry(entry)
        self._save()
        fpath.unlink()
        logger.info("staging_approved id=%s task=%s", staging_id, entry.get("task"))
        return True

    def reject_staging(self, staging_id: str) -> bool:
        """拒绝一条待审核知识。"""
        fpath = self._dir / self._STAGING_DIR / f"{staging_id}.json"
        if not fpath.exists():
            return False
        fpath.unlink()
        logger.info("staging_rejected id=%s", staging_id)
        return True

    # ── 合并与淘汰 ──

    def _merge_entry(self, new_entry: dict):
        """合并一条新知识到 entries，处理去重 + 置信度 + 上限。

        策略：
        1. 同 task 已有同类路径 → 合并 stats（取更高 confidence 的表述）
        2. 达到 max_entries_per_task → 淘汰 confidence 最低的
        3. 保留 1 条最低 confidence 做备用锚点
        """
        max_per_task = self._get_max_per_task()
        task = new_entry.get("task", "")
        new_confidence = _CONFIDENCE_ORDER.get(new_entry.get("confidence", "observed"), 0)
        new_inject = new_entry.get("inject", "").strip()

        # 收集同 task 的条目
        same_task = [e for e in self._entries if e.get("task") == task]
        others = [e for e in self._entries if e.get("task") != task]

        # 去重：是否已有本质相同的路径
        for existing in same_task:
            if _paths_equivalent(existing.get("inject", ""), new_inject):
                # 合并：取较高 confidence
                existing_conf = _CONFIDENCE_ORDER.get(existing.get("confidence", "observed"), 0)
                if new_confidence > existing_conf:
                    existing["confidence"] = new_entry.get("confidence", "candidate")
                existing["stats"]["success_count"] = existing["stats"].get("success_count", 0) + 1
                existing["stats"]["avg_tokens"] = _weighted_avg(
                    existing["stats"].get("avg_tokens", 0),
                    new_entry.get("tokens_saved_estimate", 0),
                    existing["stats"].get("success_count", 1),
                )
                self._entries = others + same_task
                return

        # 没有重复 → 新增
        same_task.append(new_entry)
        same_task.sort(
            key=lambda e: _CONFIDENCE_ORDER.get(e.get("confidence", "observed"), 0),
            reverse=True,
        )

        # 检查上限
        if len(same_task) > max_per_task:
            # 如果最低 confidence 是 observed/candidate → 淘汰
            lowest = same_task[-1]
            lowest_conf = _CONFIDENCE_ORDER.get(lowest.get("confidence", "observed"), 0)
            if lowest_conf < _CONFIDENCE_ORDER.get("recommended", 2):
                same_task = same_task[:-1]  # 淘汰最低
                logger.info("merge_evicted task=%s inject=%s", task, lowest.get("inject", "")[:40])

        # 确保至少保留 1 条做锚点（即使全是 authoritative）
        self._entries = others + same_task

    def _get_max_per_task(self) -> int:
        """获取每个任务的最大知识条目数。"""
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("max_entries_per_task", 3)
        except Exception:
            return 3

    def _save(self):
        """将当前 entries 写回 paths.json。"""
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"version": 1, "max_entries_per_task": 3, "entries": []}

        data["entries"] = self._entries
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def git_commit(self, message: str):
        """在知识库目录执行 git commit。"""
        git_dir = self._dir
        try:
            import subprocess
            subprocess.run(
                ["git", "add", "-A"],
                cwd=git_dir, capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=git_dir, capture_output=True, timeout=10,
            )
        except Exception as exc:
            logger.warning("knowledge_git_commit_failed: %s", exc)


def _normalize_task(task: str) -> str:
    """将任务名转为文件友好 ID。"""
    s = task.strip().lower()
    s = re.sub(r"[^a-z0-9一-鿿]+", "-", s)
    return s.strip("-")[:40]


def _paths_equivalent(a: str, b: str) -> bool:
    """判断两条路径描述是否本质相同。"""
    return a[:30] == b[:30] or a in b or b in a


def _weighted_avg(old_avg: float, new_val: float, count: int) -> float:
    """加权平均：新值权重 1/count。"""
    return round((old_avg * (count - 1) + new_val) / count, 1)
