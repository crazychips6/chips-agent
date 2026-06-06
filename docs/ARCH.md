# chips-agent 架构设计

## 分层依赖

```
tool/  safety/  session/  memory/  config/   ← 零内部依赖
    ↕        ↕
environment/                                ← 仅依赖 safety
    ↕        ↕
agent/                                      ← 依赖接口而非实现
```

## 解耦原则

| 原则 | 说明 |
|------|------|
| **工具零依赖** | `tool/` 不依赖其他任何模块 |
| **面向接口编程** | `agent/` 通过 protocol/ABC 依赖下层模块，而非具体类 |
| **构造注入** | AIAgent 通过 `__init__` 接收 registry、memory、environment 等依赖 |
| **自注册** | 工具通过 `registry.register()` 自注册，agent 只要 import 模块即可 |
| **单向依赖** | 禁止循环依赖，方向不可逆 |

## 模块间契约

```
ToolRegistry (tool/registry.py)
    get_definitions(tool_names) → List[dict]     # Agent 获取工具 schema
    dispatch(name, args) → str                    # Agent 派发工具调用

Environment (environment/base.py)  ← Protocol
    execute(command) → ExecuteResult              # Agent 执行命令
    close()                                       # Agent 清理

Approval (safety/approval.py)
    check(command) → ApproveResult                # Agent 调用审批

MemoryStore (memory/store.py)
    for_system_prompt() → str                     # PromptBuilder 读取快照
    add(content) → dict                           # Agent tool 写入

SessionDB (session/db.py)
    save_message(session_id, msg)                 # Agent 保存对话历史
    search(query) → List[session]                 # 独立使用

PromptBuilder (agent/prompt.py)
    build(layers_config) → str                    # 组装 system prompt
```

## 数据流

```
User Input → CLI → AIAgent.run_conversation()
                       │
                       ├─ PromptBuilder.build() → 9层 System Prompt
                       │
                       ├─ LLM API → response
                       │    ├─ 有 tool_calls → dispatch() → 回填
                       │    └─ 纯文本 → 返回用户
                       │
                       ├─ SessionDB.save_messages() (每个轮次)
                       └─ MemoryStore (仅在工具调用时写入)
```

## 编码约束

| 模块 | 允许依赖 | 禁止依赖 |
|------|---------|---------|
| `tool/` | 无 | 其他任何模块 |
| `safety/` | 无 | 其他任何模块 |
| `session/` | 无 | 其他任何模块 |
| `memory/` | 无 | 其他任何模块 |
| `config/` | 无 | 其他任何模块 |
| `environment/` | `safety/` | `agent/`, `tool/` |
| `agent/` | 所有模块的接口 | 具体实现类 |
