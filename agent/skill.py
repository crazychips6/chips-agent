"""Skill — 文件化技能系统

Skill 是 ``SKILL.md`` 文件，放在 ``~/.chips/skills/`` 目录下。
每个 skill 是一份流程说明书，LLM 通过工具按需加载。

与 Hermes 对齐的设计：
- 渐进式加载：system prompt 只放 name + 短描述
- ``skills_list`` / ``skill_view`` / ``skill_manage`` 三个工具
- Skill = markdown 正文，不是 Python 代码

目录结构::

    ~/.chips/skills/
      deploy-to-vercel/
        SKILL.md
      review-code/
        SKILL.md
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("chips.agent.skill")

# 标准 frontmatter 分隔符
_FM_DELIMITER = "---"

# YAML 多文档分隔符
_YAML_DOC_SEPARATOR = re.compile(r"^\.\.\.\s*$", re.MULTILINE)

# system prompt 中显示的描述截断长度
_DESC_MAX_LEN = 60


@dataclass
class SkillMeta:
    """Skill 的元信息（从 SKILL.md frontmatter 解析）。"""
    name: str
    description: str
    filepath: Path
    platforms: list[str] | None = None
    required_environment_variables: list[dict[str, Any]] = field(default_factory=list)


def _parse_skill_file(filepath: str | Path) -> SkillMeta | None:
    """解析 SKILL.md 文件，返回 SkillMeta。

    frontmatter 格式 (YAML)::

        ---
        name: skill-name
        description: ...
        platforms: [macos, linux]
        required_environment_variables:
          - name: API_KEY
            optional: false
        ---
        # 正文 markdown ...

    如果解析失败返回 None。
    """
    filepath = Path(filepath)
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        logger.warning("skill_read_failed path=%s", filepath)
        return None

    if not text.startswith(_FM_DELIMITER):
        logger.debug("skill_no_frontmatter path=%s", filepath)
        return None

    # 找到第二个 "---" 作为 frontmatter 结束
    end = text.find(_FM_DELIMITER, 3)
    if end == -1:
        logger.debug("skill_unclosed_frontmatter path=%s", filepath)
        return None

    fm_text = text[3:end].strip()
    try:
        fm = yaml.safe_load(fm_text)
    except Exception:
        logger.warning("skill_yaml_parse_error path=%s", filepath)
        return None

    if not isinstance(fm, dict):
        return None

    name = fm.get("name", filepath.parent.name)
    description = str(fm.get("description", ""))
    if not name or not description:
        logger.debug("skill_missing_name_or_desc path=%s", filepath)
        return None

    platforms = fm.get("platforms")
    if isinstance(platforms, list) and all(isinstance(p, str) for p in platforms):
        platforms = [p.lower() for p in platforms]
    else:
        platforms = None

    env_vars = fm.get("required_environment_variables") or fm.get("prerequisites", {}).get("env_vars", [])
    if isinstance(env_vars, list):
        env_vars = [
            {"name": v} if isinstance(v, str) else v
            for v in env_vars
        ]
    else:
        env_vars = []

    return SkillMeta(
        name=name,
        description=description,
        filepath=filepath,
        platforms=platforms,
        required_environment_variables=env_vars,
    )


def _get_skill_content(filepath: str | Path) -> str | None:
    """返回 SKILL.md 中 frontmatter 之后的正文字段。

    返回纯文本（去掉 frontmatter），如果出错返回 None。
    """
    filepath = Path(filepath)
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    if text.startswith(_FM_DELIMITER):
        end = text.find(_FM_DELIMITER, 3)
        if end != -1:
            return text[end + 3:].strip()

    return text.strip()


_SKILLS_PROMPT_HEADER = """## Skills
Before replying, scan the skills below. If a skill matches or is even partially
relevant to your task, you MUST load it with skill_view(name) and follow its
instructions.

<available_skills>
{index}
</available_skills>

Skills provided by plugins use the format ``skill_view("plugin-name:skill-name")``.
Plugin skills may not appear in the list above — try ``skills_list`` to see them all.

Only proceed without loading a skill if genuinely none are relevant to the task."""


def _build_skills_index_prompt(skills: list[SkillMeta]) -> str:
    """构建 system prompt 中的 ``<available_skills>`` 块。"""
    if not skills:
        return ""
    lines = []
    for s in skills:
        desc = s.description[:_DESC_MAX_LEN] + "..." if len(s.description) > _DESC_MAX_LEN else s.description
        lines.append(f"  - {s.name}: {desc}")
    return _SKILLS_PROMPT_HEADER.format(index="\n".join(lines))


class SkillManager:
    """文件化技能系统。

    扫描 ``~/.chips/skills/`` 目录及外部目录中的 ``SKILL.md`` 文件，
    提供列表/查看/管理功能。由 ``skills_list`` / ``skill_view`` / ``skill_manage`` 三个工具驱动。
    """

    def __init__(self):
        self._scan_dirs: list[Path] = []
        self._index: dict[str, SkillMeta] = {}  # name → meta
        self.add_default_dirs()

    def add_default_dirs(self) -> None:
        """添加默认扫描目录。"""
        self.add_scan_dir(Path.home() / ".chips" / "skills")
        self.add_scan_dir(Path("skills"))

    def add_scan_dir(self, d: str | Path) -> None:
        resolved = Path(d).resolve()
        if resolved not in self._scan_dirs and resolved.is_dir():
            self._scan_dirs.append(resolved)

    # ── 索引 ──

    def scan(self) -> int:
        """重新扫描所有目录，构建索引。返回发现的 skill 数量。"""
        self._index.clear()
        count = 0
        for scan_dir in self._scan_dirs:
            for fpath in sorted(scan_dir.rglob("SKILL.md")):
                meta = _parse_skill_file(fpath)
                if meta is None:
                    continue
                if meta.name in self._index:
                    logger.warning("skill_name_conflict name=%s path1=%s path2=%s",
                                   meta.name, self._index[meta.name].filepath, fpath)
                    continue
                self._index[meta.name] = meta
                count += 1
        return count

    # ── 查询 ──

    def list_skills(self) -> list[SkillMeta]:
        """返回所有已索引的 skill 元信息。"""
        return list(self._index.values())

    def get_skill(self, name: str) -> SkillMeta | None:
        return self._index.get(name)

    def view_skill_content(self, name: str) -> str | None:
        """返回 skill 的完整 markdown 正文。"""
        meta = self._index.get(name)
        if meta is None:
            return None
        return _get_skill_content(meta.filepath)

    def get_skills_index_prompt(self) -> str:
        """构建 ``<available_skills>`` system prompt 块。"""
        return _build_skills_index_prompt(self.list_skills())

    # ── 管理 ──

    def create_skill(self, name: str, description: str, content: str) -> Path | None:
        """创建一个新的 skill 文件。返回文件路径。"""
        # 确定存放目录：第一个有写入权限的扫描目录
        for d in self._scan_dirs:
            skill_dir = d / name
            try:
                skill_dir.mkdir(parents=True, exist_ok=True)
                fpath = skill_dir / "SKILL.md"
                fm = {
                    "name": name,
                    "description": description,
                }
                fm_text = f"---\n{yaml.dump(fm, default_flow_style=False).strip()}\n---\n"
                fpath.write_text(fm_text + "\n" + content.lstrip("\n"), encoding="utf-8")
                # 重新扫描以更新索引
                self.scan()
                return fpath
            except OSError:
                continue
        return None

    def delete_skill(self, name: str) -> bool:
        """删除一个 skill。返回是否成功。"""
        meta = self._index.get(name)
        if meta is None:
            return False
        try:
            skill_dir = meta.filepath.parent
            for f in skill_dir.iterdir():
                f.unlink()
            skill_dir.rmdir()
            self._index.pop(name, None)
            return True
        except OSError:
            return False

    # ── 属性 ──

    @property
    def count(self) -> int:
        return len(self._index)

    @property
    def skill_names(self) -> list[str]:
        return list(self._index.keys())
