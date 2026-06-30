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
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel

logger = logging.getLogger("chips.web")

# ── 配置 ──

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"

# JWT 密钥：优先从环境变量读取，否则生成随机密钥（重启后 token 失效）
_SECRET_KEY_ENV = os.getenv("CHIPS_WEB_SECRET")
if _SECRET_KEY_ENV:
    SECRET_KEY = _SECRET_KEY_ENV
else:
    import secrets
    SECRET_KEY = secrets.token_hex(32)
    logger.warning("CHIPS_WEB_SECRET 未设置，使用随机密钥（服务重启后已签发的 token 将失效）")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# 登录凭据：必须通过环境变量显式设置，无默认值
SIMPLE_USER = os.getenv("CHIPS_WEB_USER")
SIMPLE_PASS = os.getenv("CHIPS_WEB_PASS")
if not SIMPLE_USER or not SIMPLE_PASS:
    raise RuntimeError(
        "必须设置 CHIPS_WEB_USER 和 CHIPS_WEB_PASS 环境变量才能启动 Web 服务"
    )

# ── 模型 ──


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    reset: bool = False


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

# CORS：默认关闭跨域（前端同源无需 CORS），通过 CHIPS_WEB_CORS_ORIGINS 开启
# 多个 origin 用逗号分隔：http://localhost:3000,https://example.com
_CORS_ORIGINS = os.getenv("CHIPS_WEB_CORS_ORIGINS", "")
CORS_ORIGINS = [o.strip() for o in _CORS_ORIGINS.split(",") if o.strip()]
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
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


def _get_agent_or_none():
    """返回 _agent 但不初始化（用于 health/metrics 只读访问）。"""
    return _agent


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
        from tool.toolsets import resolve_multiple_toolsets
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
        agent._resolve_tool_names()

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
        agent._extra_tool_names |= plugin_mgr.plugin_tool_names

        # MCP
        mcp_servers_config = ConfigStore().read_mcp_servers()
        if mcp_servers_config:
            mcp_mgr = MCPManager(registry=registry)
            mcp_mgr.load_servers(mcp_servers_config)
            agent.mcp_manager = mcp_mgr
            agent.tool_names |= set(mcp_mgr.get_all_tool_names())
            agent._extra_tool_names |= set(mcp_mgr.get_all_tool_names())

        # ── TodoStore（模块级，供 todo 工具使用） ──
        from tool.builtins.todo_tool import TodoStore, wire_store as wire_todo_store
        wire_todo_store(TodoStore())

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
        # 初始化部署状态指标
        try:
            from gateway.metrics import set_deployment_healthy
            set_deployment_healthy("gateway")
            set_deployment_healthy("session_db")
            set_deployment_healthy("memory")
        except Exception:
            pass
        logger.info("agent_initialized")
    return _agent


# ── 生命周期 ──


@app.on_event("shutdown")
async def shutdown():
    global _agent
    if _agent is not None:
        logger.info("shutting down agent ...")
        if mcp := getattr(_agent, "mcp_manager", None):
            try:
                mcp.stop_all()
            except Exception:
                pass
        logger.info("agent shut down complete")
        _agent = None


# ── 路由 ──


@app.get("/metrics")
async def metrics_prometheus():
    """Prometheus 标准格式指标端点，供 Prometheus server 抓取。"""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return PlainTextResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/api/health")
async def health():
    """健康检查：返回各子系统状态。"""
    status = {"status": "ok", "subsystems": {}}

    # Agent 状态
    agent = _get_agent_or_none()
    if agent is None:
        status["subsystems"]["agent"] = "not_initialized"
    else:
        agent_ok = True
        agent_info = {
            "model": agent.model,
            "tools": len(agent.tool_names),
            "session_id": agent.session_id,
            "messages": len(agent.messages),
        }
        status["subsystems"]["agent"] = agent_info

    # Memory 状态
    if agent and agent.memory_manager:
        providers = [p.name for p in agent.memory_manager.providers]
        status["subsystems"]["memory"] = {
            "providers": providers,
            "active": len(providers),
        }
        try:
            from gateway.metrics import set_deployment_healthy, set_deployment_degraded
            set_deployment_healthy("memory")
        except Exception:
            pass
    else:
        status["subsystems"]["memory"] = "disabled"
        try:
            from gateway.metrics import set_deployment_down
            set_deployment_down("memory")
        except Exception:
            pass

    # Session DB 状态
    if agent and agent.session_db:
        try:
            count = agent.session_db.summary_stats().get("total_sessions", -1)
            status["subsystems"]["session_db"] = {"sessions": count, "status": "ok"}
            try:
                from gateway.metrics import set_deployment_healthy
                set_deployment_healthy("session_db")
            except Exception:
                pass
        except Exception as e:
            status["subsystems"]["session_db"] = {"status": "error", "detail": str(e)}
            try:
                from gateway.metrics import set_deployment_degraded
                set_deployment_degraded("session_db")
            except Exception:
                pass
    else:
        status["subsystems"]["session_db"] = "disabled"

    # 网关 / API 状态
    if agent and hasattr(agent, "gateway"):
        gateway = getattr(agent, "gateway", None)
        inner = getattr(gateway, "_inner", None)
        provider = type(inner).__name__ if inner else "unknown"
        status["subsystems"]["gateway"] = {"provider": provider, "status": "ok"}

    return status


@app.get("/api/metrics")
async def metrics():
    """返回 Prometheus 风格的聚合指标。"""
    agent = _get_agent_or_none()
    if agent is None:
        return {"status": "not_initialized"}

    gateway = getattr(agent, "gateway", None)
    if gateway is None or not hasattr(gateway, "get_metrics"):
        return {"status": "unavailable", "reason": "gateway not instrumented"}

    result = gateway.get_metrics()
    result["status"] = "ok"
    return result


@app.get("/api/insights/cost-by-model")
async def insights_cost_by_model(days: int = Query(7, ge=1, le=365)):
    """按模型汇总费用。"""
    from agent.insights import InsightsEngine
    agent = _get_agent_or_none()
    if agent is None or not agent.session_db:
        raise HTTPException(status_code=503, detail="agent not initialized")
    engine = InsightsEngine(agent.session_db)
    return {"insights": engine.cost_by_model(days=days).dict()}


@app.get("/api/insights/daily-cost")
async def insights_daily_cost(days: int = Query(30, ge=1, le=365)):
    """每日费用趋势。"""
    from agent.insights import InsightsEngine
    agent = _get_agent_or_none()
    if agent is None or not agent.session_db:
        raise HTTPException(status_code=503, detail="agent not initialized")
    engine = InsightsEngine(agent.session_db)
    return {"insights": engine.daily_cost_trend(days=days).dict()}


@app.get("/api/insights/tool-usage")
async def insights_tool_usage(days: int = Query(7, ge=1, le=365)):
    """工具使用统计。"""
    from agent.insights import InsightsEngine
    agent = _get_agent_or_none()
    if agent is None or not agent.session_db:
        raise HTTPException(status_code=503, detail="agent not initialized")
    engine = InsightsEngine(agent.session_db)
    return {"insights": engine.tool_usage(days=days).dict()}


@app.get("/api/insights/session/{session_id}")
async def insights_session(session_id: str):
    """单会话完整画像。"""
    from agent.insights import InsightsEngine
    agent = _get_agent_or_none()
    if agent is None or not agent.session_db:
        raise HTTPException(status_code=503, detail="agent not initialized")
    engine = InsightsEngine(agent.session_db)
    result = engine.session_portrait(session_id)
    data = result.dict()
    if not data:
        raise HTTPException(status_code=404, detail="session not found")
    return {"insights": data}


@app.get("/api/insights/weekly-report")
async def insights_weekly():
    """一键周报。"""
    from agent.insights import InsightsEngine
    agent = _get_agent_or_none()
    if agent is None or not agent.session_db:
        raise HTTPException(status_code=503, detail="agent not initialized")
    engine = InsightsEngine(agent.session_db)
    return {"insights": engine.weekly_report().dict()}


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
            if body.reset:
                agent.messages.clear()
                agent._saved_count = 0
                agent._tool_call_history.clear()
                agent.session_id = agent.session_db.create_session()
                if agent.context_engine:
                    agent.context_engine.on_session_reset()
                gateway = getattr(agent, "gateway", None)
                if gateway and hasattr(gateway, "reset"):
                    gateway.reset()
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
    if body.reset:
        agent.messages.clear()
        agent._saved_count = 0
        agent._tool_call_history.clear()
        agent.session_id = agent.session_db.create_session()
        if agent.context_engine:
            agent.context_engine.on_session_reset()
        gateway = getattr(agent, "gateway", None)
        if gateway and hasattr(gateway, "reset"):
            gateway.reset()
    chunks: list[str] = []
    agent.run_conversation(body.message, chunk_callback=lambda c: chunks.append(c))
    return {"reply": "".join(chunks)}



# ── 静态文件 ──

if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def run(host: str = "0.0.0.0", port: int = 8648):
    import uvicorn

    from agent.logger import setup_logging
    setup_logging(console=True)
    logger.info("chips web starting on http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
