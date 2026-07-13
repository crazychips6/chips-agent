# Redis 会话管理方案

## 架构

```
CLI (TypeScript)          Web (React/Next.js)
       \                    /
        \                  /
         ↘                ↙
      Agent API (Python)
      ┌──────────────────────┐
      │   FastAPI            │
      │   ┌────────────────┐ │
      │   │  Agent Engine   │ │
      │   └────────────────┘ │
      │   ┌────────────────┐ │
      │   │  Redis Store    │ │  ← 所有会话消息存 Redis
      │   │  (消息历史)     │ │
      │   └────────────────┘ │
      └──────────────────────┘
```

## Redis 数据结构

```
session:{session_id}                    # 会话元数据
  ├── created_at: "2024-01-01T00:00:00"  # 创建时间
  └── message_count: "10"                # 消息数量

session:{session_id}:history             # 消息历史（List）
  ├── {"role": "user", "content": "你好", "timestamp": "..."}
  ├── {"role": "assistant", "content": "你好！", "timestamp": "..."}
  └── ...
```

## API 端点

### 创建会话

```bash
POST /api/sessions
Response: {"session_id": "uuid"}
```

### 获取会话历史

```bash
GET /api/sessions/{session_id}/history
Response: {"messages": [{"role": "...", "content": "...", "timestamp": "..."}]}
```

### 发送消息（流式）

```bash
POST /api/chat
{
  "session_id": "uuid",
  "message": "你好"
}
Response: SSE stream
```

### 发送消息（非流式）

```bash
POST /api/chat/sync
{
  "session_id": "uuid",
  "message": "你好"
}
Response: {"reply": "你好！", "session_id": "uuid"}
```

## 启动服务

### 1. 启动 Redis

```bash
# 使用 docker-compose
docker-compose -f docker-compose.redis.yml up -d

# 或直接启动
redis-server
```

### 2. 启动 Python 后端

```bash
# 设置 Redis URL（可选，默认 redis://localhost:6379）
export REDIS_URL=redis://localhost:6379

# 启动服务
python -m web.server
```

### 3. 启动 TypeScript CLI

```bash
cd chips-tui
npm install
npm run dev
```

## 测试

### 创建会话并发送消息

```bash
# 创建会话
curl -X POST http://localhost:8648/api/sessions
# 返回: {"session_id": "abc-123"}

# 发送消息
curl -X POST http://localhost:8648/api/chat/sync \
  -H "Content-Type: application/json" \
  -d '{"session_id": "abc-123", "message": "你好"}'
# 返回: {"reply": "你好！", "session_id": "abc-123"}

# 获取历史
curl http://localhost:8648/api/sessions/abc-123/history
# 返回: {"messages": [...]}
```

## 优点

1. **消息持久化**：所有消息存储在 Redis，服务重启不丢失
2. **多客户端共享**：CLI 和 Web 可以访问同一个会话
3. **实时同步**：可以通过 WebSocket 实现实时同步
4. **高性能**：Redis 读写速度快，适合存储聊天记录
