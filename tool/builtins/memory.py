"""memory 工具 — 三级记忆读写 (working / episodic / semantic)

模块级 _store 引用由 cli.py wiring 时注入。
工具通过 registry.register() 自注册到 memory 工具集。"""

from tool.registry import registry

# wiring 时由 cli.py 注入 MemoryStore 实例
_store = None


def _read_handler(args) -> str:
    category = args.get("category", "memory")
    if not _store:
        return "记忆系统未初始化"

    if category == "working":
        wm = _store.get_working()
        if not wm:
            return "当前会话暂无记录"
        return "\n".join(f"{k}: {v}" for k, v in wm.items())

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


_CATEGORIES = ["memory", "user", "episodic", "working"]


registry.register(
    name="memory_read",
    toolset="memory",
    schema={
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": "读取记忆，包含持久记忆、历史会话摘要和当前会话工作记忆",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": _CATEGORIES,
                        "description": "memory/user=持久知识, episodic=历史会话摘要, working=当前会话笔记",
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
            "description": "保存一条记忆。working 格式为 'key: value'，episodic 保存会话摘要，memory/user 保存持久知识",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "要记忆的内容（working 分类请用 'key: value' 格式）",
                    },
                    "category": {
                        "type": "string",
                        "enum": _CATEGORIES,
                        "description": "memory/user=持久知识, episodic=会话摘要, working=当前会话临时笔记",
                    },
                },
                "required": ["content"],
            },
        },
    },
    handler=_save_handler,
)
