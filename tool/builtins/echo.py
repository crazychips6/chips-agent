"""echo 工具 — 验证工具链用

在模块级调用 registry.register() 完成自注册。这是最简单的工具实现，
用于验证从 ReAct 循环到 ToolRegistry dispatch 的完整链路。"""

from tool.registry import registry

registry.register(
    name="echo",
    toolset="test",
    schema={
        "type": "function",
        "function": {
            "name": "echo",
            "description": "回显输入文本",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "文本"},
                },
                "required": ["text"],
            },
        },
    },
    handler=lambda args: args.get("text", ""),
)
