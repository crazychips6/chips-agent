# hermes-web-ui 架构参考

源仓库：https://github.com/EKKOLearnAI/hermes-web-ui（7200+ star）

## 概述

Hermes Agent 的全功能桌面应用和 Web 管理面板。管理 AI 聊天会话、监控用量与成本、配置平台渠道、管理定时任务、浏览技能。

安装：`npm install -g hermes-web-ui && hermes-web-ui start`

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Naive UI + Pinia + Vue Router + vue-i18n + SCSS + markdown-it + highlight.js |
| BFF 后端 | Koa 2（端口 8648） |
| 桌面壳 | Electron |
| 实时通信 | Socket.IO（`/chat-run` 命名空间） |
| 终端 | node-pty + @xterm/xterm（WebSocket 传输） |
| 包管理 | npm monorepo（workspaces） |
| 认证 | JWT Token（自动生成或 `AUTH_TOKEN` 环境变量） |

## 架构图

```
浏览器 → BFF (Koa, :8648) → Socket.IO /chat-run
                ↓
        Hermes agent bridge → Hermes Agent runtime
                ↓
           Hermes CLI / profiles
           profile config.yaml    (渠道/Provider 配置)
           profile auth.json      (凭证池)
```

## Package 结构

| Package | 路径 | 职责 |
|---|---|---|
| Client | `packages/client/src` | Vue UI，路由，Pinia stores，API 封装，i18n |
| Server | `packages/server/src` | HTTP API，认证，Socket.IO，SQLite stores，文件访问，Hermes 集成 |
| Desktop | `packages/desktop` | Electron 壳，本地 Web UI 启动，更新器，打包 Hermes 运行时 |
| Skills | `packages/skills` | 内置技能 |
| Tests | `tests` | Vitest + Playwright |

## Server 内部结构

```
routes/         → HTTP 和 WebSocket 入口
controllers/    → 请求级行为
services/       → 可复用的 IO、领域行为、外部进程、集成逻辑
db/             → SQLite schema 和 stores
middleware/     → 认证等中间件
shared/         → 跨 server 常量和工具
```

## 关键功能

### AI 聊天
- Socket.IO `/chat-run` 实时流式推送
- 多会话管理，本地 SQLite 存储（不依赖 Hermes state.db）
- 来自 Telegram/Discord/Slack 等平台的会话分组
- Markdown 渲染 + 代码高亮 + 工具调用展开
- 按 profile 隔离的文件上传/下载（支持 local/Docker/SSH/Singularity backend）

### 平台渠道
一个页面统一配置 8 个平台：Telegram、Discord、Slack、WhatsApp、Matrix、飞书、微信、企业微信。凭证写入 `~/.hermes/.env`，行为设置写入 `~/.hermes/config.yaml`。

### Bridge 模式（关键设计）
聊天不走传统 REST API，而是通过 agent bridge 子进程：

```
Web UI → Socket.IO → Koa BFF → Hermes agent bridge (Python 子进程) → Hermes Agent runtime
```

bridge 是一个常驻的 Python 子进程（broker），通过 ZeroMQ（ipc/tcp）与 Hermes Agent 通信。支持自动重启、超时控制、transport 切换。

相关环境变量：
- `HERMES_AGENT_BRIDGE_ENDPOINT` — broker endpoint（默认 ipc:///tmp/hermes-agent-bridge.sock）
- `HERMES_AGENT_BRIDGE_TIMEOUT_MS` — 响应超时（默认 120000）
- `HERMES_AGENT_BRIDGE_PLATFORM` — 传给 agent 的 platform 标识

### 多 Profile 管理
- 创建/克隆/导出/切换 Hermes 配置文件
- 按 Profile 隔离：配置、缓存、上传、会话、用量、记忆、技能、插件、Provider
- 账号绑定 Profile 权限：超级管理员管理全部，普通管理员只能看分配的

### 群聊
- 多 Agent 聊天房间，Socket.IO 实时通信
- @提及路由触发特定 Agent 回复
- 上下文压缩（超 Token 阈值自动摘要）

### 用量分析
- Token 明细（输入/输出）、会话数、日均统计
- 预估费用、缓存命中率、模型分布
- 30 天每日趋势（柱状图 + 表格）

### 其他
- Cron 任务管理（创建/暂停/恢复/删除、立即触发）
- 文件浏览器（上传/下载/重命名/复制/移动/删除、语法高亮查看）
- 技能浏览和搜索
- 日志查看（Agent/Server/Error，按级别/文件/关键词过滤）
- TTS/STT 语音支持（Edge TTS / OpenAI 兼容 / 自定义 TTS）
- Web 终端（基于 node-pty + xterm.js，WebSocket 传输）

## 对 chips 的参考价值

1. **Bridge 模式** — Web UI 通过 Python 子进程桥接 agent，不是直接 HTTP 调 agent。这样 agent 能复用现有 CLI 能力（profile、plugins、skills），Web UI 不需要重新实现。
2. **Socket.IO 推流** — 不是 SSE 也不是 polling，双向实时通信，适合聊天 + 工具调用场景。
3. **BFF 层** — Koa 做 BFF，前端不直接碰 agent 数据。认证、文件、SQLite 都在 BFF 层统一。
4. **Monorepo 结构** — client/server/desktop 分离清晰，可独立开发和部署。
