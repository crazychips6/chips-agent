# chips-agent

> 通用 AI Agent 框架 · 自研完整工具链 · TUI / Web 双界面

[![Python](https://img.shields.io/badge/Python-3.12+-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)]()
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek%7CQwen-orange)]()
[![TUI](https://img.shields.io/badge/UI-TUI%20%7C%20Web-purple)]()

chips-agent 是一个从零到一独立设计的通用 Agent 框架，具备完整的工具系统、Multi-Agent 编排、上下文管理和可观测性能力。

---

## 特性

| | 特性 | 说明 |
|---|---|---|
| 🧠 | **分级处理** | 端侧小模型（Qwen 1.5B）秒级直答，复杂任务自动切换云端大模型 |
| 🔧 | **工具系统** | 自注册、model_scope 声明式可见、意图级自动过滤、延迟加载 |
| 🤖 | **Multi-Agent** | 四种协作模式 + 子 Agent 全生命周期管理（状态机/持久化/TTL） |
| 📜 | **上下文管理** | 两级压缩 + 冷冻/动态分层 prompt，长对话 token 可控 |
| 📖 | **持续学习** | 自动从执行轨迹提取经验，置信度分级后注入后续会话 |
| 📊 | **可观测性** | Prometheus 指标 + Langfuse 全链路 Trace |

---

## 快速开始

```bash
# 安装
uv sync

# 配置 API Key
export DEEPSEEK_API_KEY="sk-xxx"

# 启动 CLI
uv run chips

# 单条消息模式
uv run chips -m "你好"
```

### Ollama 端侧模型（可选）

```bash
# 连接远端 Ollama
ssh -f -N -L 11434:localhost:11434 user@remote-server

# 或本地安装
ollama pull qwen2.5:1.5b-instruct-q4_K_S
```

> 端侧模型不可用时自动降级，仅使用云端模型，不影响主流程。

---

## 架构

```
用户消息
  │
  ▼
┌─ GuardEngine ──────────────┐  安全拦截（block / pass）
└──────────┬─────────────────┘
           │
           ▼
┌─ FastLLM ──────────────────┐  端侧模型分类 + 工具预测
│  intent 识别 + 候选排序      │
│  predicted_tools 预激活      │
└──────────┬─────────────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
┌─ small ─┐  ┌─ large ──────┐
│ 直答通道  │  │ ReAct 循环    │
│ Ollama   │  │ DeepSeek/GPT │
│ ~3s 回复  │  │ 完整工具集    │
└──────────┘  └──────────────┘
```

### 工具过滤三层

```
1. model_scope → 工具声明自己该给哪个模型看（all / large）
2. intent 过滤 → greeting 零工具，web_search 只看 web
3. check_fn    → 运行时环境判断（有 .git 才暴露 git 工具）
```

---

## Multi-Agent 编排

四种协作模式：

| 模式 | 说明 |
|------|------|
| **Single** | 单步委派给指定 Agent |
| **Supervisor** | 多子任务并发/串行，汇总结果 |
| **Pipeline** | 链式接力，上一步输出作为下一步上下文 |
| **Debate** | 多 Agent 独立回答同一问题，返回对比 |

预置角色：

```
researcher   → 网络搜索与资料分析
coder        → 编写与调试代码
reviewer     → 代码审查与架构分析
shell_expert → 复杂命令与系统管理
```

### 子 Agent 生命周期

```
状态机：CREATED → RUNNING → COMPLETED / FAILED / CANCELLED
持久化：SQLite，会话恢复时自动加载历史
超时：  TTL + 后台 cleaner 线程，超时自动取消
监控：  Prometheus 指标（并发数/耗时/Token）
钩子：  生命周期事件回调（可观测性注入）
```

---

## 可观测性

| 工具 | 用途 |
|------|------|
| Prometheus | 请求量、延迟、Token 用量、路由决策、Guard 拦截 |
| Langfuse | 完整 Trace/Span 树、成本归因、按模型拆分 |

---

## 相关文档

- [路由架构](docs/ROUTING-ARCH.md) — 路由层设计与演进
- [核心参考](docs/core-reference.md) — 架构总览

---

## 技术栈

**语言：** Python 3.12+  
**LLM：** DeepSeek / OpenAI / Ollama  
**存储：** SQLite（WAL + FTS5）  
**可观测：** Prometheus / Langfuse  
**界面：** TUI（prompt_toolkit + rich） / Web（FastAPI）
