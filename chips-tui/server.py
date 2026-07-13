"""chips-tui 后端服务器。"""

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI()


class ChatRequest(BaseModel):
    text: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """处理聊天请求。"""
    # TODO: 调用 agent 逻辑
    reply = f"收到消息: {request.text}"
    return ChatResponse(reply=reply)


@app.get("/health")
async def health():
    """健康检查。"""
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
