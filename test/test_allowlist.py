"""safety/allowlist 模块测试"""

import os

import pytest
import yaml

import safety.allowlist
from safety.allowlist import (
    check, add, remove, clear, list_all, count,
)


@pytest.fixture(autouse=True)
def _temp_allowlist(tmp_path, monkeypatch):
    """使用临时文件避免污染真实白名单。"""
    path = tmp_path / "allowlist.yaml"
    monkeypatch.setattr("safety.allowlist._ALLOWLIST_PATH", path)
    yield
    if path.exists():
        path.unlink()


class TestCheck:
    def test_empty_allowlist_returns_false(self):
        assert check("sudo apt update") is False

    def test_matching_command_returns_true(self):
        add("sudo apt update")
        assert check("sudo apt update") is True

    def test_different_command_returns_false(self):
        add("sudo apt update")
        assert check("sudo apt upgrade") is False

    def test_exact_match_required(self):
        add("rm -rf /tmp/foo")
        assert check("rm -rf /tmp/foo") is True
        assert check("rm -rf /tmp/foo/") is False


class TestAdd:
    def test_add_new_entry(self):
        add("docker ps", pattern="docker")
        entries = list_all()
        assert len(entries) == 1
        assert entries[0]["command"] == "docker ps"
        assert entries[0]["pattern"] == "docker"
        assert "added_at" in entries[0]

    def test_add_duplicate_no_op(self):
        add("echo hello")
        add("echo hello")
        assert count() == 1

    def test_add_multiple_entries(self):
        add("cmd1")
        add("cmd2")
        add("cmd3")
        assert count() == 3


class TestRemove:
    def test_remove_existing(self):
        add("sudo make install")
        assert remove("sudo make install") is True
        assert check("sudo make install") is False

    def test_remove_nonexistent(self):
        assert remove("nonexistent") is False

    def test_remove_partial(self):
        add("a")
        add("b")
        remove("a")
        assert count() == 1
        assert check("b") is True


class TestClear:
    def test_clear(self):
        add("x")
        add("y")
        clear()
        assert count() == 0
        assert list_all() == []


class TestPersistence:
    def test_survives_module_reload(self):
        """条目写入 YAML 文件后可由新加载的列表读到。"""
        add("persist test")
        entries_before = list_all()
        assert len(entries_before) == 1

        # 模拟重新加载：从文件读取
        with open(safety.allowlist._ALLOWLIST_PATH) as f:
            data = yaml.safe_load(f)
        assert len(data["allowlist"]) == 1
        assert data["allowlist"][0]["command"] == "persist test"

    def test_yaml_format(self):
        add("cmd", pattern="dangerous")
        with open(safety.allowlist._ALLOWLIST_PATH) as f:
            data = yaml.safe_load(f)
        assert "allowlist" in data
        assert isinstance(data["allowlist"], list)


class TestEdgeCases:
    def test_missing_file(self):
        """文件不存在时返回 False/空。"""
        p = safety.allowlist._ALLOWLIST_PATH
        # 先确保文件存在再删除
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("dummy")
        os.remove(p)
        assert check("anything") is False
        assert list_all() == []
        assert count() == 0

    def test_corrupted_file(self, monkeypatch):
        """损坏的文件不崩溃，视为空。"""
        p = safety.allowlist._ALLOWLIST_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{{ invalid yaml")
        assert check("anything") is False
        assert list_all() == []

    def test_empty_string_command(self):
        add("")
        assert check("") is True
        assert check(" ") is False
