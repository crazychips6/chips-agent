# 插件系统信息流

## PluginContext 的角色

`PluginContext` 是 chips 传给插件的注册接口。插件不需要自己导入或实例化它，只需 `register(ctx)` 接收参数：

```python
def register(ctx):    # ctx 是 chips 传进来的 PluginContext 实例
    ctx.register_tool(name="...", schema={...}, handler=lambda args: "...")
    ctx.register_hook(MyHook())
```

PluginContext 只服务于插件的信息提取：
- `register_tool()` — 从插件提取工具 schema 和 handler，调用 `ToolRegistry.register()` 注册
- `register_hook()` — 收集 hook 实例

这跟 built-in 工具的注册方式不同。built-in 工具是工具自己的代码直接调用 `registry.register()` 注册自己，而 plugin 工具是通过 `register_tool()` 把信息交给 PluginManager，由 PluginManager 统一注册。

## 加载链路

```python
ctx = PluginContext(registry=self._registry)   # 创建空的注册上下文
register_fn(ctx)                                # 调用插件 register(ctx)
                                                #
# register_fn(ctx) 内部：
#   ctx.register_tool(name, schema, handler)    # → PluginContext 记录工具名 + 注册到 ToolRegistry
#   ctx.register_hook(MyHook())                 # → PluginContext 收集 hook 实例

# register_fn 返回后，PluginManager 从 ctx 取结果：
self._plugin_tool_names.update(ctx._tool_names) # 取走工具名
self._hook_plugins.extend(ctx._hook_plugins)    # 取走 hook 实例
```

至此，PluginManager 同时持有了工具（通过 ToolRegistry）和 hook（通过 `_hook_plugins` 列表）。

## 绕过 toolset 限制

built-in 工具通过 `resolve_toolset()` 静态选择，但 plugin 工具不在任何静态 toolset 中。解决方案是 wiring 阶段再追加：

```python
# agent/cli.py
agent.tool_names = resolve_toolset("core") & registry.tool_names  # built-in 工具
agent.tool_names |= plugin_mgr.plugin_tool_names                  # plugin 工具直接加进来
```

## 运行时链路

LLM 不区分 built-in 和 plugin 工具。返回的 `tool_calls` 对所有工具一视同仁。loop 中：

```
LLM 返回 tool_calls([{name: "sample_greet", args: {...}}])
       │
       ▼
plugin_manager.dispatch_tool_call_pre(name, args)
       │
       │  for hook in self._hook_plugins:
       │      hook.on_tool_call_pre(name, args)
       │
       ▼
registry.dispatch(name, args)          ← 统一 dispatch（不分 built-in 还是 plugin）
       │
       ▼
plugin_manager.dispatch_tool_call_post(name, result)
       │
       │  for hook in self._hook_plugins:
       │      hook.on_tool_call_post(name, result)
```

### Hook 是全局的

`_hook_plugins` 是全局列表，不区分工具。所有 hook 都会收到每个工具调用的通知，由 hook 自己判断是否处理：

```python
class MyHook:
    def on_tool_call_pre(self, tool_name, args):
        if tool_name != "my_tool":
            return None        # 不关我事，跳过
        args["extra"] = "xxx"  # 只处理自己的工具
        return args
```

所以实际效果是：**所有工具都能被 hook 拦截，但只有装了 hook 插件才真的有事情做**。这是设计取舍——全部通知的好处是 hook 可以跨工具监控（记录调用耗时、拦截截图工具等），代价是无关联的 hook 也会收到调用。

## 分层

| 模块 | 职责 |
|------|------|
| `PluginManager` | 与主流程交互的层：加载插件、在 loop 中触发 hook dispatch、收集注册结果 |
| `PluginContext` | 仅服务于插件的信息提取和注册，不参与运行时 |
| `HookPlugin` | 返回给 PluginManager 的 hook 接口，定义在协议文件中 |
| `cli.py` | 插件文件管理（install/remove/list/info），独立于运行时 |

综合来说：
- **PluginManager** 是核心调度层，负责 `run_conversation` 中的 hook 触发，以及管理插件的 register 和 hook
- **PluginContext** 只是给插件用的注册说明
- **HookPlugin** 是插件返回给 PluginManager 的钩子接口
- **CLI 层** 只负责文件管理，不参与运行

## original word
- PluginContext相当于是一个留给具体plugin的一个注册说明，通过ctx = PluginContext(registry=self._registry)，register_fn(ctx)，以及plugincontext实际还import了register，所以一个是直接使用register调用了工具（和之前的调用是两种感受，之前的tool是自己执行register，而plugin是register_tool中提取好信息再执行），另一个是将实例之后hookplugin返回，返回的两个内容全部都由plugin_manager接受，同时register已经感知到了工具。
- 在loop的run_conversion时，llm返回tool_calls（plugin在llm层面和tool没有区别，llm只会认为这两个都是工具），每个tool_name 都会执行hook操作，只是tool不会添加hook，不存在则直接返回。（比较神奇的是：每个 hook 的 on_tool_call_pre 都会收到 sample_greet 这个工具名，由 hook 自己决定要不要处理），之后是正常的dispatch。至于如何绕过之前的tool_sets限制，其实就是在wiring的时候又加到tool_names里面了。
- 综合来说，plugin_manager是和主流程交互的层，负责run当中的hook触发，以及添加plugin的register和hook。而pluginContext只服务于plugin的信息提取，hookplugin则是返回给pluginmanager的hook接口。至于cli.py就是专门负责plugin子命令。
