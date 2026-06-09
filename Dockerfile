# chips-agent — Docker 部署
FROM python:3.12-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 复制项目
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

COPY . .
RUN uv sync --no-dev --frozen

# Web 端口
EXPOSE 8648

CMD ["uv", "run", "chips", "web", "--host", "0.0.0.0", "--port", "8648"]
