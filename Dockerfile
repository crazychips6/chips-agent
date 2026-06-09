# chips-agent — Docker 部署
FROM python:3.12-slim

WORKDIR /app

# 安装 Python 依赖
COPY pyproject.toml ./
RUN pip install --no-cache-dir fastapi uvicorn[standard] python-jose python-multipart \
    mcp openai python-dotenv pyyaml tiktoken prompt_toolkit

# 复制源码
COPY . .

EXPOSE 8648

CMD ["python", "-m", "web.server"]
