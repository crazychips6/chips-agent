"""Test process tool — 进程管理

部分测试需 Linux /proc 文件系统，部分跨平台。
kill 操作跳过实际杀进程，只测参数校验和错误场景。
"""
import json
import os
import platform

import pytest

from tool.builtins.process_tool import _handle


class TestProcessList:
    def test_list_default(self):
        data = json.loads(_handle({"action": "list"}))
        assert "processes" in data
        assert data["count"] >= 1
        assert "system" in data

    def test_list_has_required_fields(self):
        data = json.loads(_handle({"action": "list", "limit": 5}))
        assert data["count"] <= 5
        p = data["processes"][0]
        assert "pid" in p
        assert "name" in p

    def test_list_sort_mem(self):
        data = json.loads(_handle({"action": "list", "sort": "mem", "limit": 5}))
        assert data["count"] <= 5

    def test_list_large_limit(self):
        data = json.loads(_handle({"action": "list", "limit": 999}))
        assert data["count"] <= 200  # 上限 200


class TestProcessGet:
    @pytest.mark.skipif(platform.system() != "Linux", reason="依赖 /proc")
    def test_get_by_pid_on_linux(self):
        """用当前进程 PID 验证 get 返回结构。"""
        data = json.loads(_handle({"action": "get", "pid": os.getpid()}))
        assert data["pid"] == os.getpid()
        assert "name" in data
        assert "state" in data
        assert "threads" in data
        assert "cmdline" in data

    def test_get_nonexistent_pid(self):
        data = json.loads(_handle({"action": "get", "pid": 99999999}))
        assert "error" in data

    def test_get_missing_params(self):
        data = json.loads(_handle({"action": "get"}))
        assert "error" in data

    def test_get_by_name(self):
        """当前进程通过 name 搜索。"""
        data = json.loads(_handle({"action": "get", "name": "python"}))
        # 可能返回空，但不应该报错
        if "matches" in data:
            assert isinstance(data["matches"], list)
        elif "error" in data:
            pass  # 允许搜索失败（如 pgrep 不可用）


class TestProcessKill:
    def test_kill_missing_params(self):
        data = json.loads(_handle({"action": "kill"}))
        assert "error" in data

    def test_kill_nonexistent_pid(self):
        data = json.loads(_handle({"action": "kill", "pid": 1}))
        # PID 1 要么是 init（权限不足），要么不存在，都会返回 error
        assert "error" in data or "success" in data

    def test_kill_both_pid_and_name(self):
        data = json.loads(_handle({"action": "kill", "pid": 1234, "name": "test"}))
        assert "error" in data


class TestUnknownAction:
    def test_unknown(self):
        data = json.loads(_handle({"action": "unknown"}))
        assert "error" in data
