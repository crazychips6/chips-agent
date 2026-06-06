"""TOOLSETS — 工具集分组与解析

工具集名 → 工具名（或其他工具集名）的映射。
resolve_toolset() 递归展开，返回扁平的工具名集合。"""

TOOLSETS: dict[str, set[str]] = {
    "core": {"echo", "terminal", "file_read", "file_write", "file_search",
             "web_fetch", "web_search", "screenshot"},
    "memory": {"memory_read", "memory_write"},
    "all": {"core", "memory"},
}


def resolve_toolset(*names: str) -> set[str]:
    """展开工具集名，返回扁平的工具名集合。

    支持递归引用（如 all → core），自动检测循环引用。
    如果名字既不是工具集名也不是工具名，直接忽略。
    """
    result: set[str] = set()
    resolving: set[str] = set()

    def _resolve(name: str):
        if name in resolving:
            return
        subset = TOOLSETS.get(name)
        if subset is None:
            result.add(name)
            return
        resolving.add(name)
        for item in subset:
            _resolve(item)
        resolving.discard(name)

    for name in names:
        _resolve(name)

    return result
