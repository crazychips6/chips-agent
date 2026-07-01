<p align="center">
  <img src="assets/banner.svg" alt="chips-agent">
</p>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/Python-3.12+-blue?logo=python&logoColor=white" alt="Python"></a>
  <a href="#"><img src="https://img.shields.io/badge/License-MIT-green" alt="License"></a>
  <a href="#"><img src="https://img.shields.io/badge/LLM-DeepSeek%7COpenAI%7COllama-orange" alt="LLM"></a>
  <a href="#"><img src="https://img.shields.io/badge/UI-TUI%20%7C%20Web-purple" alt="UI"></a>
  <a href="#"><img src="https://img.shields.io/badge/Storage-SQLite%20FTS5-003B57?logo=sqlite" alt="SQLite"></a>
</p>
[![SQLite](https://img.shields.io/badge/Storage-SQLite%20FTS5-003B57?logo=sqlite)]()

chips-agent 是一个从零到一独立设计的通用 AI Agent 框架。具备完整的工具系统、上下文管理、端侧+云端分级处理、Multi-Agent 编排与可观测性能力。

> **AI Native**——去掉 LLM 系统无法运行。LLM 是执行引擎而非功能插件。

---

## 架构总览

```
用户消息
    │
    ▼
┌───────────── GuardEngine ─────────────┐  安全拦截
│  YAML 规则匹配 → block / pass          │  block 命令、提示词注入
└──────────────────┬────────────────────┘
                   │ pass
                   ▼
┌──────────── FastLLM ──────────────────┐  意图分类 + 工具预测
│  Qwen2.5:1.5B 分类 → intent + tools   │  
│  候选池排序 + 无效标签过滤              │  
└──────────────┬────────────────────────┘
               │
          ┌────┴────┐
          ▼         ▼
┌── Small ──┐  ┌── Large ────────────┐
│ 直答通道    │  │ ReAct 循环           │  完整工具集
│ Ollama ~3s │  │ DeepSeek / GPT      │  全局 prompt
│ 无工具      │  │ 动态层 + 工具过滤     │  上下文维护
└────────────┘  └─────────────────────┘
```

### 三层工具过滤

| 层 | 机制 | 效果 |
|----|------|------|
| `model_scope` | 工具声明自己给哪个模型看 | 复杂工具（orchestrate/sub_agent）对大模型可见 |
| **intent 过滤** | 根据分类结果保留对应工具 | greeting 零工具，web_search 只看 web |
| **check_fn** | 运行时条件判断 | 无 .git 目录时不暴露 git 工具 |

---

## 特性

### 🧠 分级处理

端侧小模型（Qwen2.5:1.5B）本地秒级直答，大模型云端处理复杂任务。不可用时自动降级。

```bash
# 直答通道（greeting / simple_qa）
你   > 你好
chips > 你好！⚡   ← ~3 秒，本地处理

# 大模型通道（搜索 / 编码 / 分析）
你   > 搜索最新的 AI 框架
chips > 当前主流框架：......  ← ReAct 循环 + 工具调用
```

### 🔧 工具系统

- **自注册**：工具文件在 import 时自动调用 `registry.register()`，零配置
- **声明式可见范围**：`model_scope="large"` 的工具小模型看不到
- **延迟加载**：工具分组，LLM 通过 `tool_request` 按需激活
- **安全**：死循环检测、结果截断、异常捕获

```
core（8个，始终可用）：bash / file / web / orchestrate / clarify /
                       todo / tool_describe / tool_request
dev（4个，延迟加载）：  process / system_info / screenshot / calendar
agent（2个，延迟加载）： skill / sub_agent
```

### 🤖 Multi-Agent 编排

四种协作模式：

```
single     → 委派给单个 Agent
supervisor → 多子任务并发/串行，汇总结果
pipeline   → 链式接力，上一步输出作为下一步上下文
debate     → 多 Agent 独立回答，返回对比
```

子 Agent 全生命周期管理：

```
状态机：CREATED → RUNNING → COMPLETED / FAILED / CANCELLED
持久化：SQLite，会话恢复自动加载历史
超时：  TTL + 后台 cleaner 线程，超时自动取消
监控：  Prometheus 指标（并发数/耗时/Token）
钩子：  生命周期事件回调（可观测性注入）
```

预置 4 个角色 Agent：

```
researcher   → 网络搜索与资料分析
coder        → 编写与调试代码
reviewer     → 代码审查与架构分析
shell_expert → 复杂命令与系统管理
```

### 📜 上下文管理

- **两级压缩**：先零成本裁剪工具输出，再按 token 阈值触发的 LLM 摘要
- **分层 prompt**：冷冻层（身份/约定/记忆快照）一次构建整轮复用，动态层每轮增量注入
- **边界保护**：assistant-tool 配对校验，摘要比原文长时自动回退
- **前缀缓存**：冷冻层内容不变，DeepSeek API 缓存命中后近乎免费

### 📖 持续学习

```
ReAct 执行 → 工具调用链 → 小模型分析
    → 提取最优路径 / 失败模式
    → staging → 置信度分级（observed → authoritative）
    → 后续会话注入
```

### 📊 可观测性

| 工具 | 覆盖范围 |
|------|---------|
| **Prometheus** | 路由决策、Guard 拦截、工具调用量/延迟、Token 用量 |
| **Langfuse** | 全链路 Trace/Span 树、缓存/推理 Token 分解、按模型成本归因 |

---

## 快速开始

### 前置条件

```bash
# Python 3.12+
# 至少配置一个 API Key
export DEEPSEEK_API_KEY="sk-xxx"
```

### 安装与运行

```bash
git clone https://github.com/crazychips6/chips-agent
cd chips-agent
uv sync

# CLI 交互模式
uv run chips

# 单条消息模式
uv run chips -m "你好"

# Web 界面
export CHIPS_WEB_USER="admin"
export CHIPS_WEB_PASS="your-password"
uv run chips web
```

### 端侧模型（可选）

```bash
# 连接远端 Ollama
ssh -f -N -L 11434:localhost:11434 user@remote-server

# 或本地安装
ollama pull qwen2.5:1.5b-instruct-q4_K_S
```

端侧模型不可用时自动降级，仅使用云端模型，不影响主流程。

---

## CLI 命令

| 命令 | 说明 |
|------|------|
| `/clear` | 清除当前对话历史 |
| `/compact` | 手动触发上下文压缩 |
| `/rewind N` | 回退 N 轮对话 |
| `/agent list` | 列出所有可用 Agent 角色 |
| `/agent add <name>` | 添加自定义 Agent 角色 |

### 启动参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--model` | deepseek-v4-flash | 主模型 |
| `--toolset` | core | 启动工具集 |
| `--no-memory` | — | 禁用记忆系统 |
| `--no-compress` | — | 禁用上下文压缩 |
| `--no-stream` | — | 禁用流式输出 |
| `--env` | local | 执行环境（local/docker） |

---

## 配置文件

项目使用 `~/.chips/` 目录存储配置：

```
~/.chips/
├── routing.yaml       # 安全拦截规则（YAML）
├── agents.yaml        # Agent 角色定义
├── config.yaml        # 通用配置
└── sessions.db        # 对话历史（SQLite FTS5）
```

安全规则示例：

```yaml
rules:
  - name: "block_emergency_stop"
    priority: 5000
    when:
      intent_keyword: { in: ["紧急停止", "emergency_stop"] }
    then: block
    reason: "用户请求紧急停止"
```

Agent 角色示例：

```yaml
agents:
  researcher:
    description: "网络研究助手"
    model: deepseek-v4-flash
    tools: [web, file]
    system_prompt: "你是一位专业的研究员..."
    max_iterations: 20
```

---

## 项目结构

```
chips-agent/
├── agent/              # 核心：ReAct 循环、Prompt 组装
│   ├── loop.py         # ReAct 循环、工具派发、路由
│   ├── prompt.py       # 7 层 system prompt 组装
│   ├── sub_agent.py    # 子 Agent 生命周期管理
│   └── intent_config.py# 意图路由配置表
├── endpoint/           # 端侧模型
│   └── fast_llm.py     # FastLLM 分类器
├── safety/             # 安全层
│   ├── guard.py        # GuardEngine 安全拦截
│   └── defaults.yaml   # 默认拦截规则
├── tool/               # 工具系统
│   ├── registry.py     # 工具注册表
│   ├── toolsets.py     # 工具集定义
│   └── builtins/       # 内置工具实现
├── gateway/            # LLM API 网关
│   ├── providers/      # 多模型接入
│   └── metrics.py      # Prometheus 指标
├── session/            # 持久化
│   └── db.py           # SQLite WAL + FTS5
├── memory/             # 记忆系统
├── knowledge/          # 知识系统
└── plugins/            # 插件系统
    └── langfuse_observability.py
```

---

## 技术栈

| 层 | 技术 |
|----|------|
| 语言 | Python 3.12+ |
| LLM | DeepSeek / OpenAI / Ollama（Qwen2.5） |
| 存储 | SQLite（WAL + FTS5 全文检索） |
| 界面 | TUI（prompt_toolkit + rich） / Web（FastAPI） |
| 可观测 | Prometheus / Langfuse |
| 部署 | Docker / Docker Compose |
