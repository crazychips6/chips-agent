"""测试用 ToolPlugin 夹具 — sample_greet 工具"""
from plugins.protocol import PluginContext


class SampleGreetPlugin:
    name = "greeter"
    description = "打招呼插件"

    def tool_definitions(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "sample_greet",
                    "description": "向某人打招呼",
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
            }
        ]

    def execute(self, tool_name: str, args: dict) -> str:
        return f"Hello, {args.get('name', 'world')}!"


def register(ctx: PluginContext):
    plugin = SampleGreetPlugin()
    for schema in plugin.tool_definitions():
        tool_name = schema.get("function", schema).get("name")
        ctx.register_tool(
            name=tool_name,
            schema=schema,
            handler=lambda args, p=plugin, n=tool_name: p.execute(n, args),
        )
