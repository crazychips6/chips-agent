# chips-tui

TypeScript TUI for chips-agent (based on Mimo-code)

## 安装

```bash
npm install
```

## 运行

```bash
npm run dev
```

## 架构

- `src/index.ts` — 主入口
- `src/screens/home.tsx` — 启动界面
- `src/screens/chat.tsx` — 对话界面
- `src/components/logo.tsx` — Logo 组件
- `src/components/starry-background.tsx` — 星空背景

## 与 Python 后端通信

TypeScript CLI 通过 HTTP 与 Python 后端通信：

```
chips-tui (TypeScript)  ←→  chips (Python)
   │                           │
   └── TUI 渲染               └── Agent 逻辑
   └── 用户交互               └── LLM 调用
   └── HTTP 请求              └── HTTP 服务器
```

## 环境变量

- `CHIPS_BACKEND_URL` — Python 后端 URL（默认: http://localhost:8080）
