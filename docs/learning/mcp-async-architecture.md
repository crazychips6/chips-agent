## MCP 集成中的 async 架构讨论

### 为什么 chips 接入 MCP 时需要管理异步和线程

接入 MCP 时需要管理 async + 线程安全的**唯一原因**是：选择了使用官方 `mcp` Python SDK，而 SDK 是 async-native 的（`stdio_client`、`ClientSession` 全部是 async context manager）。

**MCP 协议本身不需要 async**。MCP 的 stdio 传输层就是：

```
→ 写入一行 JSON-RPC（Content-Length 帧）
← 读回一行 JSON-RPC
```

纯同步的 `subprocess.Popen` + `readline`/`writeline` 就能搞定。

### 实际架构

```
MCPManager（系统入口）
  ├── 上游：参与 wiring
  │     ├── 注册工具到 ToolRegistry
  │     └── 返回 tool_names 给 agent
  │
  └── 下游：生命周期管理
        ├── 为每个 MCP 服务器创建 MCPClient
        ├── start_all()   → 逐个连接
        └── stop_all()    → 逐个关闭

MCPClient（因为 async SDK 而复杂的部分）
  ├── 后台子线程，跑 asyncio 事件循环
  ├── 子线程中维护 session 连接
  └── 工具调用时需要 async ↔ sync 桥接
```

### 工具调用链路

```
主线程（同步）                        后台子线程（async）
dispatch("fs_read")
  → sync handler
    → client.call_tool("read", args)
      ├── run_coroutine_threadsafe(coro, loop)
      │                              └── session.call_tool()
      └── future.result()  ← 阻塞
                              ← 拿到结果 →
```

主线程通过 `future.result()` 阻塞等待结果，后台线程执行 `session.call_tool()`。两者串行，没有并发收益。

### Python 事件循环的本质

- 一个永不停歇的循环：检查待执行协程 → 完成 IO → 到期的定时器 → 无事可做时阻塞等待 IO 事件
- `await` 关键字让出控制权给事件循环，让它在等待期间去执行其他协程
- 默认情况下一个事件循环只跑在一个线程上，所有协程共享这个线程
- 事件循环不是业务逻辑，是**执行协程的调度器**

### async 的适用条件

chips 目前**不需要 async**：

- 瓶颈在 LLM 调用（1000ms+），工具调用相对 LLM 时延是微秒到毫秒级
- 工具之间串行执行，没有并行需求
- async 的成本：整个调用栈要改、REPL 事件循环冲突、心智负担增加

需要 async 的场景：
- 并行跑大量工具（如 Hermes 的 `delegate_task` 同时跑多个子 agent）
- 同时流式输出和处理后台任务
- 多个 provider 做 concurrent fallback

### 结论

当前 MCPClient 的后台线程 + 事件循环 + `run_coroutine_threadsafe` + Future 桥接，**纯粹是 SDK 的税**，不是 MCP 协议的内在需求。如果手写一个 50 行的同步 MCP 客户端，整个架构会更简洁。
