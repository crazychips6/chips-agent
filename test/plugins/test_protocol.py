"""Plugin 协议测试"""
from plugins.protocol import HookPlugin


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

    def test_plugin_context_dry_run(self):
        """PluginContext dry_run 模式不实际注册到 registry。"""
        from plugins.protocol import PluginContext
        from tool.registry import ToolRegistry

        r = ToolRegistry()
        ctx = PluginContext(registry=r, dry_run=True)
        ctx.register_tool(
            name="dry_tool",
            schema={"type": "function", "function": {"name": "dry_tool"}},
            handler=lambda args: "result",
        )

        # dry_run 模式下 registry 中没有工具
        assert "dry_tool" not in r.tool_names
        # 但 ctx 记录了信息
        assert "dry_tool" in ctx._tool_names
        assert len(ctx._tools_info) == 1
