---
name: chips-agent-web-ui
description: chips-agent Web 界面改造 - 基于 Agno Agent UI 二次开发
metadata:
  type: project
---

# chips-agent Web 界面

基于 Agno Agent UI (https://github.com/agno-agi/agent-ui) 改造，目录为 `chips-agent-web/`。

## 启动方式
- **后端**: `cd chips-agent && .venv/bin/chips web --host 0.0.0.0 --port 8648`
- **前端**: `cd chips-agent/chips-agent-web && pnpm dev -p 3456`

## 配色方案
继承 `chipsfun_web/` 风格：紫 #6B46C1 / 蓝 #4299E1 / 粉 #ED64A6 渐变，浅色背景。

## 对接的后端 API
- `POST /api/chat` — SSE 流式聊天 (`data: {"token":"..."}\n\n`)
- `GET /api/health` — 健康检查
- `POST /api/login` — JWT 认证
- 默认端口 8648

## 移动端适配
- 桌面：可折叠侧边栏
- 移动：侧边栏为 overlay 抽屉，顶部有汉堡菜单按钮

**Why:** Agno Agent UI 提供了成熟的 agent 聊天界面（工具调用可视化、流式渲染、Markdown 支持），直接改造比从零写快得多。

**How to apply:** 所有定制化修改集中在 `chips-agent-web/` 目录，保持与 `chips-agent/` 主项目独立。
