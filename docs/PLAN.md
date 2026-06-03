# chips-agent 实施计划

## 项目概述

基于 Hermes 核心架构蒸馏，构建一个名为 "chips" 的通用类 agent（含 harness）。

### 解耦设计

```
tool/  safety/  session/  memory/         ← 零内部依赖，最底层
    ↕        ↕
environment/                                ← 仅依赖 safety（凭证剥离）
    ↕        ↕
agent/                                      ← 依赖以上全部，但仅依赖接口
```

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

## 已完成 (Phase 0–9)

| 阶段 | 内容 | 代码量 |
|------|------|--------|
| 0 | 项目脚手架：uv 骨架、目录结构、CLI 入口 | ~50 行 |
| 1 | 最小 ReAct 循环：LLM 调用链路、7 层 system prompt | ~200 行 |
| 2 | ToolRegistry：注册/派发/check_fn、echo 工具 | ~300 行 |
| 3 | TOOLSETS：工具集分组、递归展开 | ~150 行 |
| 4 | Memory：冻结快照、原子写、记忆工具 | ~250 行 |
| 5 | System Prompt 7 层完整组装、context 文件搜索、注入检测 | ~250 行 |
| 6 | 安全审批：hardline/dangerous 分层、交互审批、凭证剥离、terminal 工具 | ~400 行 |
| 7 | Session 持久化：SQLite+WAL+FTS5、旋转日志、resume | ~400 行 |
| 8 | 沙盒环境：Environment Protocol、LocalEnvironment | ~100 行（实际在阶段 6 完成） |
| 9 | 扩展打磨：file_read/write、上下文压缩、测试覆盖补全、config.yaml 加载 | ~300 行 |

**全量测试**: 185 条，当前全部通过。版本号: `v0.2.0`。

---

## 进行中: Phase 10 — 脱离 Toy 阶段

**目标**: 解决最影响可用性的短板，使 chips 达到"可日常使用"级别。

详细计划: [PHASE-10-TOWARD-PRODUCTION.md](./PHASE-10-TOWARD-PRODUCTION.md)

### 子阶段

| 编号 | 名称 | 优先级 | 状态 |
|------|------|--------|------|
| A1 | LLM 调用链路容错 (retry/streaming/迭代处理) | ⭐最高 | ✅ **已完成** |
| A2 | 上下文压缩保护 | ⭐最高 | ✅ **已完成** |
| B1 | 子进程生命周期管理 | ⭐高 | ✅ **已完成** |
| B2 | DockerEnvironment | ⭐高 | 待开始 |
| C | 路径安全重写 | ⭐高 | 待开始 |
| D1 | 持久化审批白名单 | 中 | 待开始 |
| D2 | 审计日志 | 中 | 待开始 |
| E1 | 文件操作增强 (patch/grep/行范围) | 中 | 待开始 |
| E2 | Web 工具 | 中 | 待开始 |
| F1 | 记忆层级扩展 (working/episodic/semantic) | 中 | 待开始 |
| G1 | Rich REPL | 低 | 待开始 |
| G2 | Session 管理命令 | 低 | 待开始 |
| G3 | 配置系统 (`chips config`) | 低 | 待开始 |
| H1 | 图像理解支持 | 低 | 待开始 |

### 当前子任务: B2

待开始

---

## 未来: Phase 11 — 平台化与生态扩展

**前提**: Phase 10 完成后启动。

目标: 从终端 REPL → 多平台消息代理、从单 agent → 多 agent 协作、从手动 → 自动化调度。

详细计划: [PHASE-11-PLATFORM.md](./PHASE-11-PLATFORM.md)

| 编号 | 名称 | 说明 |
|------|------|------|
| I | Gateway | 多平台消息投递 (Slack/Discord/飞书) |
| J | 插件系统 | Plugin Protocol + 生命周期挂钩 |
| K | ACP | Agent 通信协议 + delegate 工具 |
| L | 技能与知识生态 | Skills Hub + 向量记忆 RAG |
| M | 自动化调度 | Cron + Webhook |
| N | 多模态扩展 | 语音 + 浏览器 |

---

## 项目结构

```
chips-agent/
├── pyproject.toml
├── CLAUDE.md
├── docs/
│   ├── PLAN.md                        # ← 主计划（本文）
│   ├── PHASE-10-TOWARD-PRODUCTION.md  # Phase 10 详细计划
│   ├── PHASE-11-PLATFORM.md           # Phase 11 详细计划
│   ├── core-reference.md              # Hermes 架构参考
│   ├── coding-path.md                 # 多模块开发工作流
│   ├── bad-case-surrogate-crash.md    # Bug 案例
│   ├── CLAUDE.md
│   └── learning/                      # 按阶段的学习笔记
│
├── agent/         # 核心：CLI + ReAct 循环 + Prompt 组装
├── tool/          # 工具系统：registry + toolsets + builtins
├── safety/        # 安全层：审批 + 凭证剥离
├── environment/   # 沙盒：Environment 协议 + LocalEnvironment
├── session/       # 持久化：SQLite + FTS5
├── memory/        # 记忆：快照 + 原子写
└── test/          # 测试：模块对应 test_*.py
```

## 验证方法

| 阶段 | 验证 | 预期 |
|------|------|------|
| 已完成 | `uv run pytest` | 185 条全部通过 |
| Phase 10 | 各子阶段新增测试 | 测试通过且新功能可手动验证 |
| Phase 11 | 同上 | 同上 |
