# chips-agent — Docker 部署
FROM python:3.12-slim

WORKDIR /app

# 安装 uv（比 pip 快 10-100x）
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /uvx /bin/

# 先复制依赖声明（层缓存）
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

# 再复制源码
COPY . .
RUN uv sync --no-dev --frozen

EXPOSE 8648

CMD ["uv", "run", "-m", "web.server"]
