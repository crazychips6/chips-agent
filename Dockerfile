# chips-agent — Docker 部署
FROM python:3.12

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple .

EXPOSE 8648

CMD ["python", "-m", "web.server"]
