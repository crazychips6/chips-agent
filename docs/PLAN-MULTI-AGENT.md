# 多 Agent 架构方案

> 状态: 设计阶段 | 优先级: 先扩工具集 → hot zone → 多 Agent

---

## 总览

三阶段递进，每阶段独立可用：

```
Phase 1: Agent-as-Tool (delegate_task)   ← 最小可行，一期上线
Phase 2: Agent Registry + 角色定义        ← 配置驱动，二期扩展
Phase 3: Orchestration 模式              ← 编排能力，三期完善
```

---

## Phase 1：Agent-as-Tool

### 概念

最简单的模式：**把子 Agent 包装成一个工具**。

```
用户 → 主 Agent
         │
         ├─ router tool ──→ task → 子 Agent (独立的 ReAct 循环)
         │                       ← result
         │
         └─ 普通工具调用
```

子 Agent 和主 Agent 用同一个 `AIAgent` 类，只是参数不同。

### 接口

```python
delegate_task({
    "task": "用 web_search 搜索 xxx 并整理报告",
    "tools": ["web_search", "web_fetch"],    # 给子 Agent 的工具
    "model": "deepseek-chat",                # 可选，默认同主 Agent
    "max_iterations": 15,                    # 可选，默认 10
    "context": "额外上下文"                   # 可选，注入子 Agent system prompt
})
```

### 实现

**新增 `tool/agent_tools.py`**：

```python
registry.register(name="delegate_task", ...)

def _handle(args) -> str:
    # 1. 创建子 AIAgent
    sub = AIAgent(
        model=args.get("model", parent.model),
        gateway=parent.gateway,   # 共享 gateway
    )
    
    # 2. 注入部分依赖
    sub.registry = parent.registry     # 共享工具注册表
    sub.tool_names = resolve_tools(args.get("tools", []))
    sub.memory_manager = parent.memory_manager  # 可选：共享记忆
    
    # 3. 执行子任务
    result = sub.run_conversation(
        args["task"],
        max_iterations=args.get("max_iterations", 10),
    )
    
    # 4. 记录成本（可选）
    parent._accumulate_costs(sub._cost)
    
    return result
```

### 子 Agent 的隔离

| 维度 | 策略 |
|------|------|
| Messages | 新 AIAgent → 新 messages[]，互不干扰 |
| Session | 子 Agent 创建独立 session（`session_db.create_session()`），记录 `parent_session_id` |
| Memory | 默认不共享（写操作会污染主 Agent 记忆），通过 `share_memory=true` 可选开启 |
| Gateway | 共享（复用 API key、rate limit、用量记录） |
| Tools | 由 `tools` 参数指定，不继承主 Agent 的所有工具 |
| 模型 | 默认同主 Agent，可指定其他模型（如子 Agent 用更强的模型） |

### 限制

- 串行：主 Agent 等待子 Agent 完成后才能继续
- 无通信：子 Agent 执行期间主 Agent 不介入
- 简单：适合纯任务委派，不适合协作

---

## Phase 2：Agent Registry

### 概念

用配置文件定义角色化 Agent，不再每次调用都指定参数。

```yaml
# .chips/agents.yaml
agents:
  researcher:
    model: deepseek-chat
    toolset: web
    system_prompt: |
      你是一个研究助手。
      擅长：搜索信息、抓取网页、整理报告。
      不擅长：执行代码、文件操作。
    max_iterations: 20

  coder:
    model: deepseek-chat
    toolset: terminal,file
    system_prompt: |
      你是一个编程助手。
      擅长：读写文件、执行命令、调试代码。
      不擅长：网络搜索。

  reviewer:
    model: deepseek-chat
    toolset: file
    system_prompt: |
      你是一个代码审查助手。
      只读文件，不写不改。找问题，不修问题。
```

### 接口

```python
delegate_task({
    "agent": "researcher",         # agents.yaml 中定义的名字
    "task": "搜索 xxx 的最新进展",
})

# 仍然支持内联参数覆盖
delegate_task({
    "agent": "researcher",
    "task": "...",
    "model": "claude-sonnet-4-6",  # 覆盖 registry 中的 model
})
```

### Agent 池

```yaml
# .chips/agents.yaml
agents:
  researcher:
    pool_size: 3          # 并发上限 = 3
    model: deepseek-chat
    toolset: web
```

同一 Agent 名可以并发运行多个实例，`pool_size` 控制上限。

---

## Phase 3：Orchestration 模式

### 3a — Supervisor 模式

```
Supervisor Agent
  │
  ├─ 分解任务
  ├─ 派发给子 Agent（并发或串行）
  ├─ 收集结果
  └─ 综合输出
```

```python
orchestrate({
    "mode": "supervisor",
    "goal": "调研 xxx 并编写一份使用指南",
    "plan": [
        {"agent": "researcher", "task": "搜索 xxx 的基本用法"},
        {"agent": "researcher", "task": "搜索 xxx 的最佳实践"},
        {"agent": "coder",      "task": "写一个示例脚本"},
    ],
    "parallel": True,       # 子 Agent 并发执行
})
```

### 3b — Pipeline 模式

```
Step1: Planner → 输出大纲
Step2: Writer  → 根据大纲写内容
Step3: Reviewer → 审查修改
```

```python
orchestrate({
    "mode": "pipeline",
    "stages": [
        {"agent": "planner",  "task": "规划文章结构"},
        {"agent": "writer",   "task": "根据规划写内容"},
        {"agent": "reviewer", "task": "审查并修改"},
    ],
})
```

每个 stage 的输入是前一个 stage 的输出。

### 3c — 辩论/评审模式

```
多个 Agent 各自独立回答同一问题
  → 选最优 / 投票 / 交叉评审
  → 合成最终答案
```

```python
orchestrate({
    "mode": "debate",
    "agents": ["coder", "researcher", "reviewer"],
    "task": "这个架构设计有什么问题？",
    "synthesis": "vote",       # vote | best | merge
})
```

---

## 实现路径

```
Phase 1 ────────────────────────────────────────────
  │
  ├─ tool/agent_tools.py        ← delegate_task 工具
  ├─ agent/agent_factory.py     ← 子 Agent 工厂（隔离创建逻辑）
  └─ 简单串行，能跑就行
         ↓
Phase 2 ────────────────────────────────────────────
  │
  ├─ config/agent_config.py     ← agents.yaml 解析
  ├─ agent/pool.py              ← Agent 池（并发控制）
  ├─ 注册 actor 或 MCP 形式     ← 可选：子 Agent 通过 MCP 暴露
  └─ 配置驱动，可复用
         ↓
Phase 3 ────────────────────────────────────────────
  │
  ├─ agent/orchestrator.py      ← 编排引擎
  ├─ agent/synthesizer.py       ← 结果合成
  └─ 完整的编排能力
```

---

## 为什么这么设计

| 决策 | 理由 |
|------|------|
| Phase 1 做工具模式 | 最少代码改动，复用现有 `AIAgent`、ToolRegistry、Gateway |
| 不搞独立进程 | 同进程通信零成本，子 Agent 共享 registry + gateway |
| Agent Registry 配置化 | 用户不需要写代码就能创建专用 Agent |
| 编排模式单独 stage | 避免一次性做太大，且每个阶段独立可用 |
| 主 Agent 等待子 Agent | 同进程串行最简单，并行可以后续加 |

## 不做的事

- 独立的 Agent 进程/容器 — 复杂度远超收益
- Agent 间流式通信 — 太复杂，结果传递就够
- Agent 持久化状态 — Session DB 已有，不做额外状态管理
- 图模式 DAG — pipeline 够用，DAG 需要用再加
