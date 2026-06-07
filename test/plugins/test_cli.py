"""plugins/cli.py 测试"""

import os
import shutil

import pytest

from plugins.cli import _find_plugin_file, _inspect, _get_user_plugin_dir

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class TestInspect:
    def test_inspect_tool_plugin(self):
        fpath = os.path.join(FIXTURES, "sample_tool_plugin.py")
        info = _inspect(fpath)
        assert info is not None
        assert len(info["tools"]) == 1
        assert info["tools"][0]["name"] == "sample_greet"
        assert info["hooks"] is False

    def test_inspect_hook_plugin(self):
        fpath = os.path.join(FIXTURES, "recording_hook_plugin.py")
        info = _inspect(fpath)
        assert info is not None
        assert info["hooks"] is True

    def test_inspect_invalid(self):
        fpath = os.path.join(FIXTURES, "invalid_plugin.py")
        info = _inspect(fpath)
        assert info is None

    def test_inspect_nonexistent(self):
        info = _inspect("/nonexistent.py")
        assert info is not None
        assert "errors" in info
        assert len(info["errors"]) > 0


class TestFindPluginFile:
    def test_find_existing(self, tmp_path):
        """在临时目录创建插件文件并查找。"""
        plugin_file = tmp_path / "my_tool.py"
        plugin_file.write_text("# plugin")
        # 临时将 tmp_path 加入扫描范围
        import plugins.cli as cli_mod
        original_dirs = cli_mod._get_scan_dirs
        cli_mod._get_scan_dirs = lambda: [str(tmp_path)]
        try:
            fpath = _find_plugin_file("my_tool")
            assert fpath is not None
            assert fpath.endswith("my_tool.py")
        finally:
            cli_mod._get_scan_dirs = original_dirs

    def test_find_nonexistent(self):
        assert _find_plugin_file("nonexistent_plugin_xyz") is None


class TestInstallAndRemove:
    @pytest.fixture
    def fake_env(self, tmp_path):
        """设置 fake user dir + scan dir，测试后恢复。"""
        fake_dir = str(tmp_path / "chips_plugins")
        os.makedirs(fake_dir)

        import plugins.cli as cli_mod
        originals = {
            "get_dir": cli_mod._get_user_plugin_dir,
            "scan_dirs": cli_mod._get_scan_dirs,
        }
        cli_mod._get_user_plugin_dir = lambda: fake_dir
        cli_mod._get_scan_dirs = lambda: [fake_dir]

        yield fake_dir

        cli_mod._get_user_plugin_dir = originals["get_dir"]
        cli_mod._get_scan_dirs = originals["scan_dirs"]

    def test_install_and_remove(self, fake_env):
        """安装插件 → 文件被复制 → 可检查 → 卸载 → 文件删除。"""
        src = os.path.join(FIXTURES, "sample_tool_plugin.py")

        from plugins.cli import _install_plugin, _remove_plugin

        _install_plugin(src)
        dest = os.path.join(fake_env, "sample_tool_plugin.py")
        assert os.path.isfile(dest)

        # 安装后可加载
        info = _inspect(dest)
        assert info is not None
        assert len(info["tools"]) == 1

        # 卸载
        _remove_plugin("sample_tool_plugin")
        assert not os.path.isfile(dest)

    def test_install_nonexistent(self, fake_env, capsys):
        import plugins.cli as cli_mod
        with pytest.raises(SystemExit):
            cli_mod._install_plugin("/nonexistent/file.py")
        captured = capsys.readouterr()
        assert "文件不存在" in captured.out

    def test_remove_nonexistent(self, capsys):
        from plugins.cli import _remove_plugin
        with pytest.raises(SystemExit):
            _remove_plugin("nonexistent_plugin_xyz")
        captured = capsys.readouterr()
        assert "未找到" in captured.out

    def test_install_duplicate(self, fake_env):
        src = os.path.join(FIXTURES, "sample_tool_plugin.py")
        dest = os.path.join(fake_env, "sample_tool_plugin.py")
        shutil.copy2(src, dest)

        from plugins.cli import _install_plugin
        with pytest.raises(SystemExit):
            _install_plugin(src)
