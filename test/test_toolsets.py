"""tool.toolsets 单元测试 + TOOLSETS–registry 集成测试"""

from tool.toolsets import TOOLSETS, resolve_toolset


class TestRegistryToolsetConsistency:
    """关键集成测试：TOOLSETS 与 tool registry 必须一致。

    如果某工具在 TOOLSETS 中声明但未注册，agent 加载工具集会失败，相当于工具不可用。
    反之，注册为 core 的工具也必须在 TOOLSETS 中声明，否则不会被 agent 加载。
    """

    def test_core_tools_all_registered(self):
        """core toolset 中声明的每个工具必须在 registry 中已注册。"""
        import tool.builtins  # noqa: F401 — 触发所有内置工具的自注册
        from tool.registry import registry

        core_tools = resolve_toolset("core")
        registered = set(registry._entries.keys())
        missing = core_tools - registered
        assert not missing, f"已在 TOOLSETS 但未注册: {missing}"

    def test_all_registered_tools_covered_by_toolset(self):
        """registry 中标记为 core 的工具都应在 TOOLSETS 中有对应声明。"""
        import tool.builtins  # noqa: F401
        from tool.registry import registry

        core_tools = resolve_toolset("core")
        registered_core = {name for name, e in registry._entries.items() if e.toolset == "core"}
        missing = registered_core - core_tools
        assert not missing, f"已注册 core 但不在 TOOLSETS 中: {missing}"

    def test_memory_tools_all_registered(self):
        """memory toolset 中的工具都已注册。"""
        import tool.builtins  # noqa: F401
        from tool.registry import registry

        memory_tools = resolve_toolset("memory")
        registered = set(registry._entries.keys())
        missing = memory_tools - registered
        assert not missing, f"memory TOOLSETS 但未注册: {missing}"

    def test_all_tools_in_all_toolset_registered(self):
        """递归展开 all 工具集，所有工具都已注册。"""
        import tool.builtins  # noqa: F401
        from tool.registry import registry

        all_tools = resolve_toolset("all")
        registered = set(registry._entries.keys())
        missing = all_tools - registered
        assert not missing, f"all 工具集中有未注册的工具: {missing}"


class TestResolveToolset:
    def test_core(self):
        assert resolve_toolset("core") == {"echo", "terminal", "file_read", "file_write",
                                           "file_search", "web_fetch", "web_search"}

    def test_all(self):
        """all 是 meta 工具集，递归展开所有子集。"""
        result = resolve_toolset("all")
        assert "echo" in result

    def test_multiple_names(self):
        """多个工具集名展开后求并集。"""
        result = resolve_toolset("core", "nonexistent")
        assert "echo" in result

    def test_no_args(self):
        assert resolve_toolset() == set()

    def test_unknown_name_as_tool_name(self):
        """未知名字当成工具名本身处理。"""
        assert resolve_toolset("some_tool") == {"some_tool"}

    def test_circular_reference(self):
        """循环引用不导致无限递归。"""
        # 手工注入循环引用
        TOOLSETS["a"] = {"b"}
        TOOLSETS["b"] = {"a"}
        result = resolve_toolset("a")
        # 不会无限递归，a 展开 b，b 引用 a 直接跳过
        assert result == set()
        # 清理
        del TOOLSETS["a"]
        del TOOLSETS["b"]

    def test_nested_toolset(self):
        """工具集嵌套引用另一工具集。"""
        TOOLSETS["nested"] = {"core"}
        result = resolve_toolset("nested")
        assert "echo" in result
        del TOOLSETS["nested"]
