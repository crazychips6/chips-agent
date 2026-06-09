"""chips Web 服务器 — FastAPI + SSE 流式聊天"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel

logger = logging.getLogger("chips.web")

# ── 配置 ──

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
SECRET_KEY = os.getenv("CHIPS_WEB_SECRET", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
SIMPLE_USER = os.getenv("CHIPS_WEB_USER", "admin")
SIMPLE_PASS = os.getenv("CHIPS_WEB_PASS", "admin")

# ── 模型 ──


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    username: str
    password: str


# ── 认证 ──


def create_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


# ── FastAPI ──

app = FastAPI(title="chips Web", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_current_user(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    user = verify_token(auth[7:])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user


# 可选认证：没有 token 时也允许访问（方便开发）
def optional_user(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return verify_token(auth[7:])


# ── Agent 单例 ──

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        from agent.loop import AIAgent
        from config.store import ConfigStore
        from gateway.providers.openai import OpenAIProvider
        from gateway.stats import UsageRecorder
        from memory.manager import MemoryManager
        from memory.providers.builtin import BuiltinMemoryProvider
        from plugins import PluginManager
        from plugins.mcp import MCPManager
        from session.db import SessionDB
        from tool.registry import registry
        from tool.toolsets import resolve_toolset
        import tool.builtins  # noqa: F401

        load_dotenv()

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set")

        ConfigStore().apply_to_env()

        raw_gateway = OpenAIProvider(api_key=api_key, base_url=os.getenv("CHIPS_BASE_URL", "https://api.deepseek.com"))
        recorder = UsageRecorder(
            raw_gateway,
            pricing=ConfigStore().read_pricing(),
        )
        agent = AIAgent(
            model=os.getenv("CHIPS_MODEL", "deepseek-chat"),
            stream=True,
            gateway=recorder,
        )
        agent.registry = registry
        agent.tool_names = resolve_toolset("core") & registry.tool_names

        # 记忆
        memory_dir = os.getenv("CHIPS_MEMORY_DIR", ".memory")
        mm = MemoryManager()
        mm.add_provider(BuiltinMemoryProvider(memory_dir=memory_dir))
        agent.memory_manager = mm

        # 插件
        plugin_mgr = PluginManager(registry=registry)
        plugin_mgr.add_default_paths()
        plugin_mgr.load_all()
        agent.plugin_manager = plugin_mgr
        agent.tool_names |= plugin_mgr.plugin_tool_names

        # MCP
        mcp_servers_config = ConfigStore().read_mcp_servers()
        if mcp_servers_config:
            mcp_mgr = MCPManager(registry=registry)
            mcp_mgr.load_servers(mcp_servers_config)
            agent.mcp_manager = mcp_mgr
            agent.tool_names |= set(mcp_mgr.get_all_tool_names())

        # Session
        session_db = SessionDB(db_path=".chips/sessions.db")
        agent.session_db = session_db
        agent.session_id = session_db.create_session()

        # 技能
        from agent.skill import SkillManager
        from tool.builtins.skill_tools import wire_skill_manager, wire_plugin_manager

        skill_mgr = SkillManager()
        skill_mgr.scan()
        agent.skills_index = skill_mgr.get_skills_index_prompt()
        wire_skill_manager(skill_mgr)
        wire_plugin_manager(plugin_mgr)

        _agent = agent
        logger.info("agent_initialized")
    return _agent


# ── 路由 ──


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    if body.username == SIMPLE_USER and body.password == SIMPLE_PASS:
        return TokenResponse(access_token=create_token(body.username))
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")


@app.post("/api/chat")
async def chat(body: ChatRequest, user: str | None = Depends(optional_user)):
    agent = get_agent()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def on_chunk(text: str):
        queue.put_nowait(text)

    def run():
        try:
            agent.run_conversation(body.message, chunk_callback=on_chunk)
        except Exception as e:
            queue.put_nowait(f"\n[Error: {e}]")
        finally:
            queue.put_nowait(None)

    Thread(target=run, daemon=True).start()

    async def generate() -> AsyncGenerator[str, None]:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield f"data: {json.dumps({'token': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/chat/sync")
async def chat_sync(body: ChatRequest, user: str | None = Depends(optional_user)):
    """非流式接口，适合测试。"""
    agent = get_agent()
    chunks: list[str] = []
    agent.run_conversation(body.message, chunk_callback=lambda c: chunks.append(c))
    return {"reply": "".join(chunks)}


# ── 静态文件 ──

if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def run(host: str = "0.0.0.0", port: int = 8648):
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    logger.info("chips web starting on http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
