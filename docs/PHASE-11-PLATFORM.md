# chips Phase 11 — 平台化与生态扩展

## 前提

Phase 10 完成后 chips 已达到"可日常使用"级别。Phase 11 的目标是**拓展交互维度**：从本地终端 REPL → 多平台消息代理、从单 agent → 多 agent 协作、从手动使用 → 自动化调度。

---

## 阶段 I：Gateway — 多平台消息投递

Hermes 的 `gateway/` 是整个项目最复杂的一层。chips 不需要一步到位，可以先做最小网关。

### I1 — 消息抽象层

| 模块 | 说明 |
|------|------|
| `gateway/` 目录 | 定义 MessageAdapter Protocol，统一消息收发接口 |
| `gateway/base.py` | `incoming(text, channel) → reply` 核心循环 |
| `gateway/session.py` | 每个 channel 独立 session 隔离 |

### I2 — 通道实现

| 通道 | 优先级 | 说明 |
|------|--------|------|
| Terminal | P0 | 本地 REPL 适配成 gateway 的一个 channel，不破坏现有体验 |
| Slack | P1 | Bolt SDK → 接收消息 → agent.run → 回复 |
| Discord | P2 | discord.py 机器人 |
| 飞书 | P3 | 飞书 bot API |
| WhatsApp | P3 | 云 API / Baileys |

### I3 — 通道管理

| 功能 | 说明 |
|------|------|
| `chips gateway start` | 启动 gateway 进程 |
| `chips gateway channel add/rm` | 动态增删通道 |
| `gateway/delivery.py` | 消息投递队列 + 幂等去重 |

**参考**: Hermes `gateway/` 全目录 (~20 文件)

---

## 阶段 J：插件系统

### J1 — 插件协议

| 模块 | 说明 |
|------|------|
| `plugins/` 目录 | 定义 Plugin Protocol：`on_register`、`on_tool_call_pre`、`on_tool_call_post`、`on_response` |
| `plugins/manager.py` | 扫描 `~/.chips/plugins/` + 项目内 `plugins/`，生命周期管理 |

### J2 — 挂钩点

| 挂钩 | 触发时机 | 用途 |
|------|----------|------|
| `on_tool_call_pre` | 工具调用前 | 参数校验、注入、改写 |
| `on_tool_call_post` | 工具调用后 | 结果审计、记录、转换 |
| `on_approval_request` | 审批弹窗前 | 自动审批规则 |
| `on_session_end` | 对话结束 | 后处理、通知 |

**参考**: Hermes `plugins/` + `hermes_cli/plugins.py` + `hermes_cli/plugins_cmd.py`

---

## 阶段 K：Agent 通信协议 (ACP)

### K1 — ACP Server

| 模块 | 说明 |
|------|------|
| `acp_adapter/server.py` | 基于 JSON-RPC 或 SSE 的 ACP 服务端 |
| `acp_adapter/tools.py` | 暴露 chips 工具集为 ACP 可调用资源 |

### K2 — ACP Client（delegate 工具）

| 模块 | 说明 |
|------|------|
| `tool/builtins/delegate.py` | 新工具：向另一个 ACP agent 发送任务，接收结果 |
| `acp_adapter/auth.py` | client/server 双向认证 |

### K3 — MoA / 子 Agent 编排

| 功能 | 说明 |
|------|------|
| `agent/orchestrator.py` | 拆分任务 → 派发子 agent → 合并结果 |
| checkpoint/resume | 子 agent 长任务可中断恢复 |

**参考**: Hermes `acp_adapter/` 全目录 (~9 文件) + `delegate_tool.py` + `mixture_of_agents_tool.py`

---

## 阶段 L：技能与知识生态

### L1 — 技能 Hub

| 功能 | 说明 |
|------|------|
| `chips skill search` | 搜索本地/远程技能 |
| `chips skill install/uninstall` | 安装卸载技能包 |
| `skills/hub.py` | 技能元数据解析 + 依赖校验 |

### L2 — 技能包格式

```yaml
# ~/.chips/skills/send-email/skill.yaml
name: send-email
version: 1.0.0
description: 通过 SMTP 发送邮件
tools:
  - send_email: "tool/builtins/email.py"
prompt: |
  当你需要发送邮件时使用 send_email 工具。
```

### L3 — 向量记忆（RAG）

| 模块 | 说明 |
|------|------|
| `memory/vector.py` | 基于 sqlite-vec 或 lightweight embedding 的语义搜索 |
| `memory/auto_extract.py` | 每轮对话后自动提取关键信息向量化存入 |
| `agent/prompt.py` | 新增 RAG 层：查询相关历史自动注入 |

---

## 阶段 M：自动化调度

### M1 — Cron 调度

| 模块 | 说明 |
|------|------|
| `cron/scheduler.py` | 定时触发 agent 执行预设任务 |
| `cron/jobs.py` | 持久化 job 定义（YAML） |
| `chips cron add/list/rm` | 管理 cron job |

### M2 — Watchdog 模式

| 功能 | 说明 |
|------|------|
| 文件变更监听 | `watchdog` 监听目录变更 → 触发 agent 分析 |
| Webhook 接收 | 接收 GitHub webhook → 自动分析 PR |

---

## 阶段 N：多模态扩展

### N1 — 语音

| 功能 | 说明 |
|------|------|
| `tool/builtins/voice.py` | STT（Whisper）+ TTS（Edge TTS / OpenAI TTS） |
| 语音交互模式 | `chips --voice` 对话式语音交互 |

### N2 — 浏览器自动化（可选）

| 功能 | 说明 |
|------|------|
| `tool/builtins/browser.py` | Playwright/CDP 封装：截图、点击、输入、导航 |

---

## 实施顺序

```
I (Gateway) ──→ 基础通道（Terminal + Slack）
                │
J (Plugin) ─────┤
                │
K (ACP) ────────┤──→ 并行推进，互相独立
                │
L (Skills/RAG) ─┤
                │
M (Cron) ────────┘
                │
N (Multimodal) ──→ 锦上添花，需要时再做
```

- **I1**（消息抽象层）是所有 gateway 通道的前置依赖
- **J + K + L** 完全可并行开发
- **M** 依赖 J（插件系统）提供 cron job 的工具执行环境

---

## 交付标准

与 Phase 10 一致：**每个子阶段有对应测试，全量测试通过，新增功能可手动验证**。

预期 chips 代码量：Phase 10 后 ~8-10K 行 → Phase 11 后 ~20-25K 行（Hermes 约 54 万行）。
