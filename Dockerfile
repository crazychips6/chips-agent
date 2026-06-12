# ===== Stage 1: 构建前端 =====
FROM node:22-alpine AS frontend

WORKDIR /app

# 复制前端项目
COPY chips-agent-web/ /app/

# 安装 pnpm 并构建（使用国内镜像源）
RUN corepack enable && corepack prepare pnpm@11 --activate
RUN pnpm install --registry https://registry.npmmirror.com --ignore-scripts
RUN pnpm build

# 验证构建产物
RUN ls -la /app/out/

# ===== Stage 2: 运行后端 =====
FROM python:3.12-slim

WORKDIR /app

# 复制后端代码（排除前端源码减少层大小）
COPY agent/ /app/agent/
COPY config/ /app/config/
COPY gateway/ /app/gateway/
COPY memory/ /app/memory/
COPY plugins/ /app/plugins/
COPY safety/ /app/safety/
COPY session/ /app/session/
COPY tool/ /app/tool/
COPY web/ /app/web/
COPY pyproject.toml /app/

# 复制前端构建产物
COPY --from=frontend /app/out/ /app/web/static/

# 验证 static 目录
RUN ls -la /app/web/static/ && ls -la /app/web/static/_next/static/ 2>/dev/null || echo "no _next dir"

# 安装 Python 依赖
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple .

EXPOSE 8648

CMD ["python", "-m", "web.server"]
