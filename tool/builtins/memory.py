"""memory 工具 — 持久化记忆读写

模块级 _store 引用由 cli.py wiring 时注入。
工具通过 registry.register() 自注册到 memory 工具集。"""

from tool.registry import registry

# wiring 时由 cli.py 注入 MemoryStore 实例
_store = None


def _read_handler(args) -> str:
    category = args.get("category", "memory")
    if not _store:
        return "记忆系统未初始化"
    content = _store.for_system_prompt()
    return content if content else "暂无记忆"


def _save_handler(args) -> str:
    if not _store:
        return "记忆系统未初始化"
    content = args.get("content", "")
    if not content:
        return "内容不能为空"
    category = args.get("category", "memory")
    result = _store.add(content, category)
    if result["status"] == "ok":
        return f"已保存到 {result['category']}"
    return f"保存失败: {result.get('message', '未知错误')}"


registry.register(
    name="memory_read",
    toolset="memory",
    schema={
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": "读取持久化记忆，包含项目记忆和用户信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["memory", "user"],
                        "description": "记忆分类，默认 memory",
                    },
                },
            },
        },
    },
    handler=_read_handler,
)

registry.register(
    name="memory_write",
    toolset="memory",
    schema={
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "保存一条持久化记忆，重启后仍然保留",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "要记忆的内容",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["memory", "user"],
                        "description": "记忆分类，memory=项目记忆, user=用户信息",
                    },
                },
                "required": ["content"],
            },
        },
    },
    handler=_save_handler,
)
