"""Plugin 协议测试"""
from plugins.protocol import HookPlugin, ToolPlugin


class TestToolPluginProtocol:
    """验证 ToolPlugin 协议的结构兼容性。"""

    def test_duck_type_conformance(self):
        """鸭式类型满足 ToolPlugin 协议。"""

        class MyPlugin:
            name = "test"
            description = "test"

            def tool_definitions(self):
                return []

            def execute(self, tool_name, args):
                return ""

        assert isinstance(MyPlugin(), ToolPlugin)

    def test_minimal_plugin(self):
        """只实现必需属性即可通过检查。"""

        class Minimal:
            name = "minimal"
            description = ""
            def tool_definitions(self): return []
            def execute(self, tool_name, args): return ""

        assert isinstance(Minimal(), ToolPlugin)

    def test_missing_name_fails(self):
        """缺少 name 属性不通过。"""

        class NoName:
            description = ""
            def tool_definitions(self): return []
            def execute(self, tool_name, args): return ""

        assert not isinstance(NoName(), ToolPlugin)


class TestHookPluginProtocol:
    """验证 HookPlugin 协议的结构兼容性。"""

    def test_duck_type_conformance(self):
        """鸭式类型满足 HookPlugin 协议。"""

        class MyHook:
            def on_register(self, registry): pass
            def on_tool_call_pre(self, tool_name, args): return None
            def on_tool_call_post(self, tool_name, result): return None
            def on_response(self, response): return None
            def on_session_end(self, messages): pass

        assert isinstance(MyHook(), HookPlugin)

    def test_partial_hook_not_protocol(self):
        """缺少任一方法则不通过。"""

        class Partial:
            def on_register(self, registry): pass

        assert not isinstance(Partial(), HookPlugin)

    def test_on_register_optional(self):
        """on_register 在 ToolPlugin 中没有要求。"""
        from plugins.manager import PluginManager
        from tool.registry import ToolRegistry

        r = ToolRegistry()
        pm = PluginManager(registry=r)

        class PluginWithoutRegister:
            name = "no_register"
            description = ""
            def tool_definitions(self): return []
            def execute(self, tool_name, args): return ""

        # 不应报错
        pm._register_tool_plugin(PluginWithoutRegister())
        assert "no_register" in pm.tool_plugin_names
