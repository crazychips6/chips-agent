# ===== Stage 1: 构建前端 =====
FROM node:22-alpine AS frontend

WORKDIR /app

# 安装 pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

# 只复制依赖文件，利用 Docker 缓存
COPY chips-agent-web/package.json chips-agent-web/pnpm-lock.yaml chips-agent-web/pnpm-workspace.yaml /app/
WORKDIR /app

RUN pnpm install --frozen-lockfile

# 复制源码并构建
COPY chips-agent-web/ /app/
RUN pnpm build

# ===== Stage 2: 运行后端 =====
FROM python:3.12-slim

WORKDIR /app

# 只复制后端需要的文件
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

# 复制前端构建产物到 static 目录（覆盖原有占位文件）
COPY --from=frontend /app/out/ /app/web/static/

# 安装 Python 依赖
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple .

EXPOSE 8648

CMD ["python", "-m", "web.server"]
