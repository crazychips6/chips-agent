# chips-agent — Docker 部署
FROM python:3.12-slim

WORKDIR /app

# 安装 gcc（tiktoken 需要编译）
RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# 复制并安装
COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8648

CMD ["python", "-m", "web.server"]
