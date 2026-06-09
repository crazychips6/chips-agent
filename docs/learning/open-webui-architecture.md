# Open WebUI 架构参考

源仓库：https://github.com/open-webui/open-webui

## 概述

Open WebUI 是一个自托管的 LLM Web 对话界面，支持 Ollama 和 OpenAI 兼容 API。功能包括 RAG、多用户管理、工具系统、插件管道。

安装：`pip install open-webui && open-webui serve` 或 Docker

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | SvelteKit + Tailwind CSS + PWA |
| 后端 | FastAPI (Python) |
| 数据库 | SQLite（默认）/ PostgreSQL 17（生产） |
| ORM | SQLAlchemy + Alembic 迁移 |
| 缓存 | Redis（分布式缓存 + session 管理） |
| 向量库 | ChromaDB（默认）/ Qdrant / Elasticsearch / FAISS |
| 实时通信 | WebSocket（逐 token 流式推送） |
| 认证 | JWT + OAuth + LDAP + RBAC |

## 架构图

```
┌──────────────────────┐
│   Frontend SvelteKit  │ ← HTTP REST + WebSocket
├──────────────────────┤
│   Backend FastAPI     │ ← Ollama / OpenAI API / Pipelines
├──────────────────────┤
│   Storage Layer       │ ← SQLite/PostgreSQL + Redis + Vector DB
└──────────────────────┘
```

## Backend 结构

```
backend/open_webui/
├── main.py           # FastAPI 入口，middleware，router 注册
├── routers/          # API 模块（ollama, openai, chats, users, documents, models）
├── models/           # SQLAlchemy ORM 模型（User, Chat, Document, Model 等）
├── utils/            # 认证、RBAC、聊天处理、模型管理、RAG 检索
├── internal/
│   ├── db.py         # 数据库连接 & session 管理
│   ├── migrations/   # Alembic 迁移脚本
│   └── wrappers.py   # 自定义 DB 封装（自动重连）
├── socket/           # WebSocket 处理
├── functions.py      # Python function calling / tool 支持
├── tasks.py          # 后台任务管理
├── constants.py
└── env.py            # 环境变量加载
```

## 关键架构特点

### 1. 工具系统（Tools）

Open WebUI 的 Tool 是 Python 类，通过以下机制可被 LLM 调用：

```python
class MyTool:
    """Title: MyTool
       Author: user
       Version: 1.0
    """
    def __init__(self):
        self.valves = Valves(...)  # 用户级配置

    async def my_function(self, arg: str, __event_emitter__):
        await __event_emitter__({"type": "status", "data": {"description": "运行中..."}})
        return result
```

- 元数据写在 class docstring 中
- `Valves` / `UserValves`（基于 Pydantic）管理 admin/用户级配置
- `EventEmitter` 向 UI 推送实时状态
- 工具在对话中由 LLM 按 function calling 协议自动调用

### 2. Pipelines 插件框架

- 独立的 OpenAI 兼容插件架构
- 支持在请求链中注入自定义逻辑（限流、翻译、监控、过滤）
- 可独立部署，不耦合核心

### 3. RAG 管道

- 文档加载（PDF、文本、图片）
- Embedding 模型（SentenceTransformer → Transformers）
- Reranker 提升检索精度
- Web search 集成（SearXNG、Google PSE、Brave、DuckDuckGo）

### 4. 认证与权限

- JWT Token + OAuth + LDAP
- RBAC 角色权限（admin/user）
- 多用户隔离

## Deployment

| 方式 | 命令 |
|---|---|
| pip | `pip install open-webui && open-webui serve` |
| Docker | `docker run -p 3000:8080 ghcr.io/open-webui/open-webui:main` |
| GPU | `:cuda` 镜像（NVIDIA GPU） |
| Bundled | `:ollama` 镜像（内置 Ollama） |

## Open WebUI vs hermes-web-ui

| 维度 | Open WebUI | hermes-web-ui |
|---|---|---|
| 后端 | FastAPI (Python) | Koa (Node.js) |
| 前端 | SvelteKit | Vue 3 |
| 定位 | 通用 LLM 聊天界面 | Hermes Agent 专用管理面板 |
| 工具系统 | Python class 注册 | 通过 Hermes agent bridge 复用 agent 工具 |
| 多 Agent | 不支持 | 群聊 @路由 + Profile 隔离 |
| RAG | 内置（多种向量库） | 无 |
| Agent 对接 | 无，纯 LLM API 调用 | Hermes Agent bridge（Python 子进程） |

## 对 chips 的参考价值

1. **Tool 系统设计** — 用 Python class + docstring 声明 metadata，`Valves` 管理配置，`EventEmitter` 做实时状态推送。chips 的工具注册可以借鉴这种声明式风格。
2. **直接对接 LLM API** — Open WebUI 不包装 agent，直接调 LLM API。chips 如果要走 Web，也可以考虑两种模式并存：简单聊天走直连（低延迟），复杂任务走 agent（全能力）。
3. **RAG 管道** — Open WebUI 的 RAG 实现（文档加载 → embedding → rerank → web search）是成熟参考，chips 需要时可以直接参照。
4. **Pipelines 插件** — 独立部署的插件服务，不耦合核心。和 chips 的 plugin 系统是互补思路：chips 插件是进程内注册，Open WebUI pipelines 是进程外 filter。
