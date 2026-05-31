"""tool.toolsets 单元测试"""

import pytest
from tool.toolsets import TOOLSETS, resolve_toolset


class TestResolveToolset:
    def test_core(self):
        assert resolve_toolset("core") == {"echo"}

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
