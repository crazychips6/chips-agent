"""测试用 ToolPlugin 夹具 — sample_greet 工具"""
from plugins.protocol import ToolPlugin


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


__plugin__ = SampleGreetPlugin()
