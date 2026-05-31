# chips-agent 实施计划

## Context

基于 Hermes 核心架构蒸馏，构建一个名为 "chips" 的通用类 agent（含 harness）。
项目当前处于零代码阶段，仅有架构参考文档。
要求按照**最小化逐渐拓展**的方式实现，各模块之间**强解耦**。

## 解耦设计

### 依赖方向（自底向上）

```
tool/  safety/  session/  memory/         ← 零内部依赖，最底层
    ↕        ↕
environment/                                ← 仅依赖 safety（凭证剥离）
    ↕        ↕
agent/                                      ← 依赖以上全部，但仅依赖接口
```

### 解耦原则

| 原则 | 说明 |
|------|------|
| **工具零依赖** | `tool/` 不依赖其他任何模块 |
| **面向接口编程** | `agent/` 通过 protocol/ABC 依赖下层模块，而非具体类 |
| **构造注入** | AIAgent 通过 `__init__` 接收 registry、memory、environment 等依赖 |
| **自注册** | 工具通过 `registry.register()` 自注册，agent 只要 import 模块即可 |
| **单向依赖** | 禁止循环依赖，方向不可逆 |

### 模块间契约

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

---

## 项目结构

```
chips-agent/
├── pyproject.toml
├── CLAUDE.md
├── docs/
│   ├── CLAUDE.md
│   ├── core-reference.md
│   └── PLAN.md
├── log/
│   └── coding_log/           # 每次 coding 的记录
│
├── agent/                    # ★ Agent 核心 — 依赖其他模块的接口
│   ├── __init__.py
│   ├── cli.py                # CLI 入口（组合根）
│   ├── loop.py               # AIAgent（ReAct 循环）
│   └── prompt.py             # PromptBuilder（7 层组装）
│
├── tool/                     # ★ 工具系统 — 零依赖
│   ├── __init__.py
│   ├── registry.py           # ToolRegistry 单例 + ToolEntry
│   ├── toolsets.py           # TOOLSETS 静态定义 + resolve_toolset()
│   └── builtins/             # 内置工具实现
│       ├── __init__.py
│       ├── echo.py           # 首个验证工具
│       └── memory.py         # 记忆工具
│
├── safety/                   # ★ 安全层 — 零依赖
│   ├── __init__.py
│   ├── approval.py           # 危险命令审批
│   └── sanitize.py           # 凭证剥离 + 日志脱敏
│
├── environment/              # ★ 沙盒环境 — 仅依赖 safety
│   ├── __init__.py
│   ├── base.py               # Environment Protocol（ABC）
│   └── local.py              # LocalEnvironment（子进程隔离）
│
├── session/                  # ★ 会话持久化 — 零依赖
│   ├── __init__.py
│   └── db.py                 # SessionDB（SQLite + WAL + FTS5）
│
├── memory/                   # ★ 记忆系统 — 零依赖
│   ├── __init__.py
│   └── store.py              # MemoryStore（冻结快照 + 原子写）
│
└── test/
    ├── __init__.py
    └── test_*.py
```

---

## 分阶段实施

### 阶段 0：项目脚手架 ✅

**目标**：搭起 uv 项目骨架，验证 package 可导入、chips 命令可用。

- 创建 `pyproject.toml`（flat layout，依赖 `anthropic`）
- 创建所有模块目录：`agent/`, `tool/`, `safety/`, `environment/`, `session/`, `memory/`, `test/`
- 创建最小 `agent/cli.py`：`argparse` + `--help`
- 创建 `log/coding_log/`
- 验证：`uv run chips --help` 和 `uv run chips --version`

### 阶段 1：最小 ReAct 循环

**目标**：一条 LLM 调用链路。无工具、无记忆、无安全。

- `agent/cli.py`：argparse 解析 `--model`，交互式 `input()` 循环
- `agent/loop.py`：`AIAgent.__init__(api_key, model)` + `run_conversation(text) → str`
- `agent/prompt.py`：`PromptBuilder.build()` 返回默认系统身份
- 配置自动加载 `~/.chips/config.yaml`
- **验证**：`uv run chips` → 输入"你好" → LLM 回复

### 阶段 2：ToolRegistry + 工具派发

**目标**：工具注册、ReAct 循环中派发 tool_calls。

- `tool/registry.py`：
  - `register()` / `dispatch()` / `get_definitions()`
  - check_fn TTL 缓存 30s，线程安全（RLock + generation counter）
- `tool/builtins/echo.py`：模块级 `registry.register()`
- `agent/loop.py`：集成 tool_calls → `registry.dispatch()` → 继续循环
- **解耦点**：agent 不直接 import 任何工具，只通过 `dispatch()` 调用
- **验证**：输入 "echo hello" → agent 调用 echo 工具 → 返回 "hello"

### 阶段 3：TOOLSETS 工具选择层

**目标**：按工具集分组，CLI 可选择启用哪些。

- `tool/toolsets.py`：`TOOLSETS` + `resolve_toolset()` 递归展开
- `agent/cli.py`：新增 `--toolset` 参数（默认 `core`）
- **验证**：`uv run chips --toolset core` 只加载 core 工具集

### 阶段 4：Memory 冻结快照

**目标**：agent 可读写持久化记忆。

- `memory/store.py`：`MemoryStore` 双文件（MEMORY.md + USER.md）、原子写、冻结快照
- `tool/builtins/memory.py`：记忆读写工具
- `agent/prompt.py`：注入记忆快照
- **验证**：记忆 → 重启 → 回忆

### 阶段 5：System Prompt 完整组装

**目标**：7 层 system prompt，上下文文件加载和注入检测。

- `agent/prompt.py`：7 层独立方法、context 文件搜索链、injection 检测、head/tail 截断
- **验证**：`--verbose` 观察 7 层内容

### 阶段 6：安全审批

**目标**：危险命令检测 + 交互审批 + 凭证剥离。

- `safety/approval.py`：HARDLINE_PATTERNS + DANGEROUS_PATTERNS + 交互审批
- `safety/sanitize.py`：EnvBlocklist + RedactingFormatter
- **验证**：模拟危险命令触发审批

### 阶段 7：Session 持久化 + 日志

**目标**：对话历史自动持久化，结构化日志。

- `session/db.py`：SQLite WAL + FTS5
- 日志系统：旋转日志 + Session 标记 + 脱敏
- **验证**：`--resume` 恢复历史

### 阶段 8：沙盒环境

**目标**：安全执行终端命令。

- `environment/base.py`：Environment Protocol
- `environment/local.py`：子进程隔离 + 凭证剥离
- `tool/builtins/terminal.py`：terminal 工具
- **验证**：`ls -la` 返回目录列表

### 阶段 9：扩展 + 打磨

- 更多内置工具、DockerEnvironment、Context 压缩、测试覆盖

---

## 进度跟踪

| 阶段 | 状态 | 完成日期 |
|------|------|---------|
| 0—项目脚手架 | ✅ | 2026-05-28 |
| 1—最小 ReAct | ✅ | 2026-05-28 |
| 2—ToolRegistry | ✅ | 2026-05-28 |
| 3—TOOLSETS | ✅ | 2026-05-30 |
| 4—Memory | ✅ | 2026-05-31 |
| 5—System Prompt | ❌ | |
| 6—安全审批 | ❌ | |
| 7—Session+日志 | ❌ | |
| 8—沙盒环境 | ❌ | |
| 9—扩展打磨 | ❌ | |

## 验证方法

| 阶段 | 验证 | 预期 |
|------|------|------|
| 0 | `uv run chips --version` | 输出版本 |
| 1 | `uv run chips` → 输入"你好" | LLM 回复 |
| 2 | 输入"echo hello" | 工具调用成功 |
| 3 | `--toolset core` | 仅加载 core |
| 4 | 记忆→重启→回忆 | 持久化生效 |
| 5 | `--verbose` 观察 system prompt | 7 层完整 |
| 6 | 模拟危险命令 | 触发审批提示 |
| 7 | `--resume` | 恢复历史 |
| 8 | 终端命令 | 子进程执行 |
| 9 | `uv run pytest` | 全部通过 |
