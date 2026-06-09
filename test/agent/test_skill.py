"""Skill 模块测试 — 文件化技能系统"""
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from agent.skill import (
    SkillManager,
    SkillMeta,
    _parse_skill_file,
    _build_skills_index_prompt,
)


def _make_skill_dir(tmpdir: str, name: str, desc: str, body: str = "# Content",
                    platforms: list[str] | None = None,
                    env_vars: list[dict] | None = None) -> str:
    """在临时目录下创建一个 SKILL.md 文件。"""
    skill_dir = os.path.join(tmpdir, name)
    os.makedirs(skill_dir, exist_ok=True)
    fm = {"name": name, "description": desc}
    if platforms:
        fm["platforms"] = platforms
    if env_vars:
        fm["required_environment_variables"] = env_vars
    fm_text = f"---\n{yaml.dump(fm, default_flow_style=False).strip()}\n---\n"
    path = os.path.join(skill_dir, "SKILL.md")
    with open(path, "w") as f:
        f.write(fm_text + "\n" + body)
    return path


class TestParseSkillFile:
    def test_parse_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_skill_dir(tmp, "test-skill", "A test skill")
            meta = _parse_skill_file(path)
            assert meta is not None
            assert meta.name == "test-skill"
            assert meta.description == "A test skill"
            assert meta.platforms is None

    def test_parse_with_platforms(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_skill_dir(tmp, "linux-only", "Linux", platforms=["linux"])
            meta = _parse_skill_file(path)
            assert meta is not None
            assert meta.platforms == ["linux"]

    def test_parse_with_env_vars(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_vars = [{"name": "API_KEY", "optional": False}]
            path = _make_skill_dir(tmp, "needs-env", "Needs env", env_vars=env_vars)
            meta = _parse_skill_file(path)
            assert meta is not None
            assert len(meta.required_environment_variables) == 1
            assert meta.required_environment_variables[0]["name"] == "API_KEY"

    def test_parse_no_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            fpath = os.path.join(tmp, "SKILL.md")
            with open(fpath, "w") as f:
                f.write("# Just content")
            meta = _parse_skill_file(fpath)
            assert meta is None

    def test_parse_missing_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            fpath = os.path.join(tmp, "SKILL.md")
            with open(fpath, "w") as f:
                f.write("---\nname:\ndescription: only desc\n---\n# Content")
            meta = _parse_skill_file(fpath)
            assert meta is None


class TestSkillManager:
    def test_scan_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SkillManager()
            mgr.add_scan_dir(tmp)
            count = mgr.scan()
            assert count == 0

    def test_scan_finds_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_skill_dir(tmp, "skill-a", "First")
            _make_skill_dir(tmp, "skill-b", "Second")
            mgr = SkillManager()
            mgr.add_scan_dir(tmp)
            assert mgr.scan() == 2
            assert mgr.count == 2
            assert set(mgr.skill_names) == {"skill-a", "skill-b"}

    def test_list_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_skill_dir(tmp, "test", "Test skill")
            mgr = SkillManager()
            mgr.add_scan_dir(tmp)
            mgr.scan()
            skills = mgr.list_skills()
            assert len(skills) == 1
            assert skills[0].name == "test"

    def test_get_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_skill_dir(tmp, "my-skill", "My description")
            mgr = SkillManager()
            mgr.add_scan_dir(tmp)
            mgr.scan()
            meta = mgr.get_skill("my-skill")
            assert meta is not None
            assert meta.description == "My description"
            assert mgr.get_skill("nonexistent") is None

    def test_view_skill_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_skill_dir(tmp, "test", "Test", body="# Instructions\n\n1. First step\n2. Second step")
            mgr = SkillManager()
            mgr.add_scan_dir(tmp)
            mgr.scan()
            content = mgr.view_skill_content("test")
            assert content is not None
            assert "# Instructions" in content
            assert "First step" in content

    def test_view_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SkillManager()
            mgr.add_scan_dir(tmp)
            mgr.scan()
            assert mgr.view_skill_content("nothing") is None

    def test_create_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SkillManager()
            mgr.add_scan_dir(tmp)
            mgr.scan()
            assert mgr.count == 0

            fpath = mgr.create_skill("new-skill", "A new skill", "# Do stuff")
            assert fpath is not None
            assert mgr.count == 1
            assert mgr.get_skill("new-skill") is not None

            ok = mgr.delete_skill("new-skill")
            assert ok is True
            assert mgr.count == 0
            assert mgr.get_skill("new-skill") is None

    def test_delete_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SkillManager()
            mgr.add_scan_dir(tmp)
            mgr.scan()
            assert mgr.delete_skill("nothing") is False


class TestBuildSkillsIndexPrompt:
    def test_empty(self):
        assert _build_skills_index_prompt([]) == ""

    def test_with_skills(self):
        skills = [
            SkillMeta(name="a", description="Skill A", filepath=""),
            SkillMeta(name="b", description="Skill B with longer description", filepath=""),
        ]
        result = _build_skills_index_prompt(skills)
        assert "<available_skills>" in result
        assert "a: Skill A" in result
        assert "b: Skill B" in result
        assert "longer" in result

    def test_long_description_truncated(self):
        long_desc = "x" * 100
        skills = [SkillMeta(name="t", description=long_desc, filepath="")]
        result = _build_skills_index_prompt(skills)
        assert "..." in result
        # 60 chars + "..." = 63
        assert "x" * 60 + "..." in result

    def test_get_skills_index_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_skill_dir(tmp, "s1", "Skill one")
            _make_skill_dir(tmp, "s2", "Skill two")
            mgr = SkillManager()
            mgr.add_scan_dir(tmp)
            mgr.scan()
            prompt = mgr.get_skills_index_prompt()
            assert "s1" in prompt
            assert "s2" in prompt
            assert "<available_skills>" in prompt
