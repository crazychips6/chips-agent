"""screenshot 工具测试 — mock subprocess，验证截屏流程"""

import os
from unittest.mock import patch, MagicMock

import pytest

from tool.registry import registry


@pytest.fixture(autouse=True)
def ensure_registered():
    """确保截图工具已注册。"""
    import tool.builtins  # noqa: F401
    # 截图工具名可能会变，注册后检查
    assert "screenshot" in registry.tool_names


TEST_DIR = os.path.join(os.path.dirname(__file__), "..", ".chips", "screenshots")


class TestScreenshotTool:
    def test_tool_registered(self):
        assert "screenshot" in registry.tool_names
        # 通过 dispatch 验证工具存在（无参调用触发截图 handler，mock 后正常返回）
        with patch("tool.builtins.screenshot.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock()
            result = registry.dispatch("screenshot", {})
        assert "截图已保存" in result

    def test_capture_success(self):
        """mock subprocess.run 成功返回，验证输出包含路径。"""
        with patch("tool.builtins.screenshot.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock()
            result = registry.dispatch("screenshot", {})

        assert "截图已保存" in result
        assert ".png" in result
        assert "screenshot-" in result

    def test_capture_all_methods_fail(self):
        """所有截图方法都失败时返回错误提示。"""
        with patch("tool.builtins.screenshot.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("not found")
            result = registry.dispatch("screenshot", {})

        assert "错误" in result
        assert "未找到" in result

    def test_capture_timeout(self):
        """截图超时也回退到下一方法。"""
        with patch("tool.builtins.screenshot.subprocess.run") as mock_run:
            import subprocess
            mock_run.side_effect = subprocess.TimeoutExpired("import", 10)
            result = registry.dispatch("screenshot", {})

        assert "错误" in result

    def test_screenshots_dir_created(self):
        """截屏目录自动创建。"""
        import tempfile
        import shutil
        temp_chips = tempfile.mkdtemp()

        with patch("tool.builtins.screenshot.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock()
            with patch("tool.builtins.screenshot._SCREENSHOT_DIR",
                       os.path.join(temp_chips, "screenshots")):
                result = registry.dispatch("screenshot", {})

        # 目录已创建
        assert os.path.isdir(os.path.join(temp_chips, "screenshots"))
        shutil.rmtree(temp_chips)

    def test_filename_has_timestamp(self):
        """文件名包含时间戳。"""
        import re
        with patch("tool.builtins.screenshot.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock()
            path = registry.dispatch("screenshot", {})
        match = re.search(r"screenshot-(\d{8}-\d{6})", path)
        assert match is not None, f"未找到时间戳: {path}"

    def test_no_args_required(self):
        """不传参数也能正常调用。"""
        with patch("tool.builtins.screenshot.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock()
            result = registry.dispatch("screenshot", {})
        assert "截图已保存" in result
