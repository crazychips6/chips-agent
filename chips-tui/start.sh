#!/bin/bash

# 启动 Python 后端
echo "启动 Python 后端..."
python server.py &
BACKEND_PID=$!

# 等待后端启动
sleep 2

# 启动 TypeScript CLI
echo "启动 TypeScript CLI..."
npm run dev

# 清理
kill $BACKEND_PID 2>/dev/null
