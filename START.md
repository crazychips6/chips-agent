# chips 智能启动

## 使用方法

### 启动 CLI

```bash
# 自动启动后端（如果未运行）+ 启动 CLI
./chips cli

# 或者
python start.py cli
```

### 启动 Web

```bash
# 自动启动后端（如果未运行）+ 启动 Web
./chips web

# 或者
python start.py web
```

### 只启动后端

```bash
# 只启动后端服务
./chips backend

# 或者
python start.py backend
```

## 智能行为

1. **自动检测后端状态**
   - 如果后端已运行 → 直接启动前端
   - 如果后端未运行 → 先启动后端，再启动前端

2. **Redis 降级**
   - 如果 Redis 可用 → 使用 Redis 存储会话消息
   - 如果 Redis 不可用 → 自动降级到内存存储
   - 无需手动配置，系统自动处理

3. **端口检测**
   - 默认端口: 8648
   - 可通过环境变量 `CHIPS_PORT` 修改

## 环境变量

```bash
# 后端配置
CHIPS_HOST=0.0.0.0
CHIPS_PORT=8648

# Redis 配置（可选）
REDIS_URL=redis://localhost:6379

# 认证配置
CHIPS_WEB_USER=admin
CHIPS_WEB_PASS=admin
```

## 快速开始

### 1. 启动 CLI（自动处理 Redis 降级）

```bash
./chips cli
```

### 2. 启动 Web（自动处理 Redis 降级）

```bash
./chips web
```

### 3. 可选：启动 Redis（如果需要持久化）

```bash
docker-compose -f docker-compose.redis.yml up -d
```

## Redis 降级机制

当 Redis 不可用时，系统会自动降级到内存存储：

```
启动后端
    │
    ▼
检测 Redis 连接
    │
    ├─ 成功 → 使用 Redis 存储
    │
    └─ 失败 → 降级到内存存储
                │
                ▼
        显示警告: redis_unavailable, using_memory_storage
```

### 降级后的行为

- 会话消息保存在内存中
- 服务重启后消息会丢失
- 适合开发和测试环境

### 恢复 Redis

如果 Redis 恢复可用，需要重启后端服务才能切换回 Redis 存储。

## 架构图

```
./chips cli/web
       │
       ▼
┌─────────────────────────────────────┐
│  start.py (智能启动脚本)             │
│  ├─ 检测后端状态                     │
│  ├─ 启动后端（如果需要）              │
│  └─ 启动前端                         │
└─────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────┐
│  Python 后端 (FastAPI)               │
│  ├─ Agent Engine                    │
│  ├─ Session Manager                 │
│  │   ├─ Redis (如果可用)             │
│  │   └─ Memory (降级)               │
│  └─ HTTP API                        │
└─────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────┐
│  前端                                │
│  ├─ CLI (TypeScript + @opentui)     │
│  └─ Web (React/Next.js)            │
└─────────────────────────────────────┘
```
