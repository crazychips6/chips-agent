#

## TOOLSETS 工具集设计

### 工具集的目的：范围控制，不只是分类

TOOLSETS 对工具做分组管理，提供 `resolve_toolset()` 递归展开能力。但本质是**范围控制**，主要有三个目的：

1. **节省 Token** — 不需要的工具定义不发给 LLM，减少请求体积
2. **能力边界** — `core` 工具集只有 echo，LLM 只能 echo；切换到带 `terminal` 的工具集才有执行命令的能力
3. **安全隔离** — 只加载当前场景需要的工具，避免 LLM 选错或滥用

### 当前实现方式：显式维护

```python
TOOLSETS = {
    "core": {"echo"},
    "all": {"core"},
}
```

- 所有工具集定义集中在 `tool/toolsets.py`
- `resolve_toolset("core")` → `{"echo"}`
- `resolve_toolset("all")` → `{"echo"}`（递归展开 core）
- CLI 通过 `--toolset` 参数选择工具集，wiring 时过滤

## 工具控制方式
- agent.tool_names = resolve_toolset(args.toolset) & registry.tool_names   
if self.registry:
    tools = self.registry.get_definitions(self.tool_names)
    if tools:
        kwargs["tools"] = tools
