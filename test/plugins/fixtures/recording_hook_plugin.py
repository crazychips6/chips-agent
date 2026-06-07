"""测试用 HookPlugin 夹具 — 记录所有钩子调用"""
from plugins.protocol import HookPlugin, PluginContext


class RecordingHook:
    """记录每次钩子调用，用于断言。"""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def on_register(self, registry) -> None:
        self.calls.append(("on_register", (), {"registry": registry}))

    def on_tool_call_pre(self, tool_name: str, args: dict) -> dict | None:
        self.calls.append(("on_tool_call_pre", (tool_name,), {"args": args}))
        return None

    def on_tool_call_post(self, tool_name: str, result: str) -> str | None:
        self.calls.append(("on_tool_call_post", (tool_name,), {"result": result}))
        return None

    def on_response(self, response: str) -> str | None:
        self.calls.append(("on_response", (), {"response": response}))
        return None

    def on_session_end(self, messages: list) -> None:
        self.calls.append(("on_session_end", (), {"messages": messages}))


def register(ctx: PluginContext):
    ctx.register_hook(RecordingHook())
