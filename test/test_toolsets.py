"""tool.toolsets 单元测试 + toolset–registry 集成测试"""

import pytest

from tool.toolsets import TOOLSET_SCHEMA, resolve_toolset, resolve_multiple_toolsets, get_toolset


class TestRegistryToolsetConsistency:
    """关键集成测试：toolset 定义与 tool registry 必须一致。"""

    def _all_tool_names(self) -> set[str]:
        """收集 TOOLSET_SCHEMA 中所有直写工具名（不递归 includes）。"""
        names: set[str] = set()
        for ts in TOOLSET_SCHEMA.values():
            names.update(ts.get("tools", []))
        return names

    def test_all_declared_tools_are_registered(self):
        """所有 toolset 中声明的工具必须在 registry 中已注册。"""
        import tool.builtins  # noqa: F401
        from tool.registry import registry

        declared = self._all_tool_names()
        registered = set(registry._entries.keys())
        missing = declared - registered
        assert not missing, f"在 TOOLSET_SCHEMA 中声明但未注册: {missing}"

    def test_core_resolves_to_real_tools(self):
        """core 展开后包含实际注册的工具。"""
        import tool.builtins  # noqa: F401
        from tool.registry import registry

        core_tools = set(resolve_toolset("core"))
        registered = set(registry._entries.keys())
        overlap = core_tools & registered
        assert len(overlap) >= 5, f"core 只解析到 {len(overlap)} 个注册工具"

    def test_all_tools_in_all_toolset_registered(self):
        """递归展开 all 工具集，所有工具都已注册。"""
        import tool.builtins  # noqa: F401
        from tool.registry import registry

        all_tools = set(resolve_toolset("all"))
        registered = set(registry._entries.keys())
        missing = all_tools - registered
        assert not missing, f"all 工具集中有未注册的工具: {missing}"


class TestResolveToolset:
    @pytest.fixture(autouse=True)
    def _ensure_tools_registered(self):
        """确保内置工具已注册到全局 registry。"""
        import tool.builtins  # noqa: F401

    def test_core_contains_echo(self):
        result = resolve_toolset("core")
        assert "echo" in result

    def test_core_includes_terminal_tools(self):
        result = resolve_toolset("core")
        assert "terminal" in result

    def test_core_includes_file_tools(self):
        result = resolve_toolset("core")
        assert "file_read" in result
        assert "file_write" in result
        assert "file_search" in result

    def test_core_includes_web_tools(self):
        result = resolve_toolset("core")
        assert "web_fetch" in result
        assert "web_search" in result

    def test_core_includes_vision(self):
        result = resolve_toolset("core")
        assert "screenshot" in result

    def test_core_includes_skills(self):
        result = resolve_toolset("core")
        assert "skills_list" in result

    def test_all(self):
        """all 是 meta 工具集，递归展开所有子集。"""
        result = resolve_toolset("all")
        assert "echo" in result
        assert "terminal" in result

    def test_multiple_names(self):
        """多个工具集合成。"""
        result = resolve_multiple_toolsets(["terminal", "file"])
        assert "terminal" in result
        assert "file_read" in result

    def test_empty_names(self):
        assert resolve_multiple_toolsets([]) == []

    def test_unknown_name_returns_empty(self):
        """未知名返回空列表（不是当作工具名原样返回）。"""
        assert resolve_toolset("some_tool") == []

    def test_circular_reference(self):
        """循环引用不导致无限递归。"""
        TOOLSET_SCHEMA["a"] = {"description": "test", "tools": [], "includes": ["b"]}
        TOOLSET_SCHEMA["b"] = {"description": "test", "tools": [], "includes": ["a"]}
        result = resolve_toolset("a")
        assert result == []
        del TOOLSET_SCHEMA["a"]
        del TOOLSET_SCHEMA["b"]

    def test_nested_toolset(self):
        """工具集嵌套引用另一工具集。"""
        TOOLSET_SCHEMA["nested"] = {"description": "test", "tools": [], "includes": ["core"]}
        result = resolve_toolset("nested")
        assert "echo" in result
        del TOOLSET_SCHEMA["nested"]

    def test_get_toolset_static(self):
        ts = get_toolset("terminal")
        assert ts is not None
        assert "description" in ts
        assert "终端" in ts["description"]

    def test_get_toolset_unknown(self):
        assert get_toolset("nonexistent_xyz") is None
