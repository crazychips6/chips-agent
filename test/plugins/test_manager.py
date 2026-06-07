"""PluginManager 单元测试"""

import os

import pytest

from plugins.manager import PluginManager
from tool.registry import ToolRegistry

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def clean_registry():
    return ToolRegistry()


class TestDiscovery:
    def test_discover_empty_dir(self, tmp_path):
        pm = PluginManager()
        pm.add_scan_path(str(tmp_path))
        assert pm.discover() == []

    def test_discover_no_dir(self):
        pm = PluginManager()
        pm.add_scan_path("/nonexistent/path")
        assert pm.discover() == []

    def test_discover_skips_init(self):
        pm = PluginManager()
        pm.add_scan_path(FIXTURES)
        found = pm.discover()
        names = [os.path.basename(f) for f in found]
        assert "__init__.py" not in names
        assert "sample_tool_plugin.py" in names

    def test_discover_dedup(self):
        """已加载的文件不再出现在 discover 结果中。"""
        pm = PluginManager()
        pm.add_scan_path(FIXTURES)
        sample = os.path.join(FIXTURES, "sample_tool_plugin.py")
        pm._loaded_files.add(sample)
        found = pm.discover()
        assert sample not in found


class TestLoad:
    def test_load_tool_plugin(self, clean_registry):
        pm = PluginManager(registry=clean_registry)
        pm.add_scan_path(FIXTURES)
        fpath = os.path.join(FIXTURES, "sample_tool_plugin.py")
        assert pm.load(fpath) is True
        assert "greeter" in pm.tool_plugin_names

        # 工具注册到 registry
        assert "sample_greet" in clean_registry.tool_names

        # dispatch 正常
        result = clean_registry.dispatch("sample_greet", {"name": "chips"})
        assert result == "Hello, chips!"

    def test_load_hook_plugin(self):
        pm = PluginManager()
        pm.add_scan_path(FIXTURES)
        fpath = os.path.join(FIXTURES, "recording_hook_plugin.py")
        assert pm.load(fpath) is True
        assert pm.hook_count == 1

    def test_load_invalid_plugin(self):
        pm = PluginManager()
        pm.add_scan_path(FIXTURES)
        fpath = os.path.join(FIXTURES, "invalid_plugin.py")
        assert pm.load(fpath) is False

    def test_load_nonexistent_file(self):
        pm = PluginManager()
        assert pm.load("/nonexistent.py") is False

    def test_duplicate_load(self, clean_registry):
        pm = PluginManager(registry=clean_registry)
        pm.add_scan_path(FIXTURES)
        fpath = os.path.join(FIXTURES, "sample_tool_plugin.py")
        assert pm.load(fpath) is True
        count_before = pm.loaded_count
        assert pm.load(fpath) is True  # 第二次返回 True（已加载）
        assert pm.loaded_count == count_before  # 不重复计数

    def test_load_all(self, clean_registry):
        pm = PluginManager(registry=clean_registry)
        pm.add_scan_path(FIXTURES)
        count = pm.load_all()
        assert count >= 2  # sample_tool + recording_hook
        assert "sample_greet" in clean_registry.tool_names

    def test_load_corrupted_file(self, tmp_path):
        bad = tmp_path / "broken.py"
        bad.write_bytes(b"\xff\xfe\x00\x01")
        pm = PluginManager()
        pm.add_scan_path(str(tmp_path))
        assert pm.load(str(bad)) is False


class TestHasTool:
    def test_has_tool_true(self, clean_registry):
        pm = PluginManager(registry=clean_registry)
        pm.add_scan_path(FIXTURES)
        pm.load_all()
        assert pm.has_tool("sample_greet") is True

    def test_has_tool_false(self, clean_registry):
        pm = PluginManager(registry=clean_registry)
        assert pm.has_tool("nonexistent") is False


class TestHookDispatch:
    @pytest.fixture
    def pm_with_hook(self):
        pm = PluginManager()
        pm.add_scan_path(FIXTURES)
        pm.load(os.path.join(FIXTURES, "recording_hook_plugin.py"))
        return pm

    def test_tool_call_pre_passthrough(self, pm_with_hook):
        """默认返回 None 时不修改参数。"""
        result = pm_with_hook.dispatch_tool_call_pre("echo", {"text": "hi"})
        assert result == {"text": "hi"}

    def test_tool_call_post_passthrough(self, pm_with_hook):
        result = pm_with_hook.dispatch_tool_call_post("echo", "ping")
        assert result == "ping"

    def test_response_passthrough(self, pm_with_hook):
        result = pm_with_hook.dispatch_response("hello")
        assert result == "hello"

    def test_session_end_no_crash(self, pm_with_hook):
        pm_with_hook.dispatch_session_end([])

    def test_multiple_hooks(self, tmp_path):
        """多个 HookPlugin 文件依次加载。"""
        code = '''\
from plugins.protocol import HookPlugin

class H:
    def on_register(self, r): pass
    def on_tool_call_pre(self, n, a): return None
    def on_tool_call_post(self, n, r): return None
    def on_response(self, r): return None
    def on_session_end(self, m): pass

__plugin__ = H()
'''
        f1 = tmp_path / "hook_a.py"
        f2 = tmp_path / "hook_b.py"
        f1.write_text(code)
        f2.write_text(code)

        pm = PluginManager()
        pm.add_scan_path(str(tmp_path))
        pm.load_all()
        assert pm.hook_count == 2
        pm.dispatch_tool_call_pre("echo", {"text": "hi"})
        # 两个 hook 都执行，不报错

    def test_hook_modifies_args(self):
        """HookPlugin 可通过返回 dict 修改 args。"""

        class ModifyArgsHook:
            def on_tool_call_pre(self, tool_name, args):
                args["modified"] = True
                return args
            def on_tool_call_post(self, tool_name, result): return None
            def on_response(self, response): return None
            def on_session_end(self, messages): pass
            def on_register(self, registry): pass

        class ModifierPlugin:
            name = "modifier"
            description = ""
            def tool_definitions(self): return []
            def execute(self, tool_name, args): return ""

        pm = PluginManager()
        pm._hook_plugins.append(ModifyArgsHook())
        result = pm.dispatch_tool_call_pre("echo", {"text": "hi"})
        assert result["modified"] is True


class TestLoadAll:
    def test_add_default_paths(self, tmp_path, monkeypatch):
        """默认路径包括 ~/.chips/plugins 和 ./plugins。"""
        monkeypatch.chdir(str(tmp_path))
        os.makedirs("plugins")
        pm = PluginManager()
        pm.add_default_paths()
        assert len(pm._scan_paths) >= 1  # 至少 ./plugins

    def test_load_no_paths(self):
        pm = PluginManager()
        assert pm.load_all() == 0

    def test_counters(self, clean_registry):
        pm = PluginManager(registry=clean_registry)
        pm.add_scan_path(FIXTURES)
        pm.load_all()
        assert pm.loaded_count >= 2
        assert len(pm.tool_plugin_names) >= 1
        assert pm.hook_count >= 1
