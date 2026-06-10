"""Test system_info tool

依赖 /proc 文件系统（Linux），Windows 跳过。
"""

import json
import platform

import pytest

from tool.builtins.system_info import _handle


@pytest.mark.skipif(platform.system() != "Linux", reason="依赖 /proc 文件系统")
class TestSystemInfoLinux:
    def test_returns_valid_json(self):
        result = _handle({})
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_os_info(self):
        data = json.loads(_handle({}))
        os_info = data["os"]
        assert os_info["system"] == "Linux"
        assert isinstance(os_info["hostname"], str)
        assert os_info["architecture"] in ("x86_64", "aarch64", "armv7l")
        assert "distro" in os_info

    def test_cpu_info(self):
        data = json.loads(_handle({}))
        cpu = data["cpu"]
        assert cpu["logical_cores"] >= 1
        assert cpu["logical_cores"] <= 512  # sanity
        assert isinstance(cpu.get("model"), str)

    def test_memory_info(self):
        data = json.loads(_handle({}))
        mem = data["memory"]
        assert mem["total_gb"] > 0
        assert mem["total_gb"] < 10000  # sanity
        assert 0 <= mem["usage_pct"] <= 100

    def test_disk_info(self):
        data = json.loads(_handle({}))
        disks = data["disk"]
        assert len(disks) >= 1
        root = disks[0]
        assert root["mount"] == "/"
        assert root["total_gb"] > 0
        assert 0 <= root["usage_pct"] <= 100

    def test_uptime(self):
        data = json.loads(_handle({}))
        assert "uptime" in data
        assert "d" in data["uptime"] or "h" in data["uptime"] or "m" in data["uptime"]

    def test_python_info(self):
        data = json.loads(_handle({}))
        py = data["python"]
        assert "version" in py
        assert "executable" in py
