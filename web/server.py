"""chips Web 服务器 — FastAPI + SSE 流式聊天 + QuickApp API"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from typing import Any, AsyncGenerator

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


class DraftRequest(BaseModel):
    description: str


class CreateRequest(BaseModel):
    draft: dict


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
        toolset_cfg = os.getenv("CHIPS_TOOLSET", "core")
        ts_names = [n.strip() for n in toolset_cfg.split(",")]
        agent.permanent_toolsets = list(ts_names)
        from tool.toolsets import CORE_ALWAYS_ON
        agent.tool_names = (CORE_ALWAYS_ON | set(resolve_multiple_toolsets(ts_names))) & registry.tool_names

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

        # ── toolset 工具接线 ──
        from tool.builtins.toolset_tool import wire_agent as wire_toolset_agent
        wire_toolset_agent(agent)

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

        # QuickApp
        from tool.builtins.quick_app_tools import wire_agent as wire_quick_app_agent
        wire_quick_app_agent(agent)
        from quick_app.manager import init_manager as init_quick_app_manager
        qa_mgr = init_quick_app_manager()
        agent.quick_app_manager = qa_mgr

        _agent = agent
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


# ═══════════════════════════════════════════════════════════════
# QuickApp 创建 — SSE 流式进度
# ═══════════════════════════════════════════════════════════════


def _qa_log(queue: asyncio.Queue, level: str, message: str, data: str = "", lang: str = "text"):
    """发送一条日志事件到 SSE 队列。"""
    try:
        queue.put_nowait(json.dumps({"type": "log", "level": level, "message": message, "data": data, "lang": lang}, ensure_ascii=False))
    except Exception:
        pass


def _qa_step(queue: asyncio.Queue, step_id: str, status: str, message: str = "", detail: str = ""):
    """发送一条进度步骤事件到 SSE 队列。"""
    try:
        queue.put_nowait(json.dumps({
            "type": "step", "id": step_id, "status": status,
            "message": message, "detail": detail,
        }, ensure_ascii=False))
    except Exception:
        pass


@app.post("/api/quick_apps/create/stream")
async def quick_apps_create_stream(
    body: CreateRequest,
    user: str | None = Depends(optional_user),
):
    """基于 draft 创建快应用，SSE 流式推送进度日志。"""
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    draft_data = body.draft

    def run():
        try:
            # ── 0. 校验 draft ──
            required = ["name", "description", "source_type", "source_name"]
            for field in required:
                if field not in draft_data:
                    queue.put_nowait(json.dumps({"type": "result", "success": False, "error": f"draft 缺少必要字段：{field}"}))
                    return

            from quick_app.models import Draft, SourceType, VenvLevel, build_schema
            from quick_app.manager import get_manager, QuickAppManager
            from tool.builtins.quick_app_tools import _check_source_validity, _build_llm_code_prompt, _clean_llm_response
            from quick_app.codegen import _indent_body, generate_code
            from quick_app.models import QUICK_APP_TEMPLATE
            from quick_app.trace_log import write as _tw

            try:
                source_type = SourceType(draft_data["source_type"])
            except ValueError:
                queue.put_nowait(json.dumps({"type": "result", "success": False, "error": f"不支持的 source_type：{draft_data['source_type']}"}))
                return

            draft = Draft(
                name=draft_data["name"], description=draft_data["description"],
                source_type=source_type, source_name=draft_data["source_name"],
                source_reason=draft_data.get("source_reason", ""),
                params=draft_data.get("params", []),
                output_description=draft_data.get("output_description", ""),
                venv_level=VenvLevel(draft_data.get("venv_level", 0)),
                install_hint=draft_data.get("install_hint", ""),
            )

            # ── 1. 双重校验 ──
            _qa_step(queue, "validate", "running", "校验方案", "检查 source 有效性")
            validation_error = _check_source_validity(draft)
            if validation_error:
                _qa_log(queue, "error", "校验不通过", validation_error)
                queue.put_nowait(json.dumps({"type": "result", "success": False, "error": validation_error}))
                return
            _qa_step(queue, "validate", "done", "校验通过", "source 有效")
            _qa_log(queue, "info", "方案验证", f"来源: {source_type.value} / {draft.source_name}")
            _tw("CREATE", "校验通过", {"name": draft.name, "source": f"{source_type.value}/{draft.source_name}", "params": [p["name"] for p in draft.params]})

            # ── 1.5 GitHub 项目提前克隆（供 LLM 生成代码时参考文件结构） ──
            repo_structure = ""
            if source_type == SourceType.GITHUB and draft.source_name:
                _qa_step(queue, "clone", "running", "克隆 GitHub 项目", f"git clone {draft.source_name}")
                from quick_app.github_deploy import _clone_repo, scan_repo_structure
                from quick_app.manager import QUICK_APPS_DIR
                repo_dir = QUICK_APPS_DIR / draft.name / "repo"
                clone_ok = _clone_repo(draft.source_name, repo_dir)
                if clone_ok:
                    _qa_step(queue, "clone", "done", "克隆完成", "扫描文件结构")
                    repo_structure = scan_repo_structure(repo_dir)
                    _qa_log(queue, "info", "Repo 结构", repo_structure[:300])
                else:
                    _qa_log(queue, "error", "克隆失败", "将尝试不依赖 repo 信息生成代码")

            # ── 2. LLM 代码生成（含 repo 结构信息） ──
            _qa_step(queue, "codegen", "running", "调用 LLM 生成代码", "构建 prompt 并发送给 LLM")
            agent = get_agent()
            prompt = _build_llm_code_prompt(draft)
            if repo_structure:
                prompt += f"\n\n克隆的仓库文件结构：\n{repo_structure}\n\n请基于以上文件结构，生成能正确导入并使用该仓库的 Python 代码。"
            _qa_log(queue, "debug", "LLM Prompt", prompt, "text")

            code = ""
            last_error = ""
            try:
                response = agent.gateway.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=agent.model, max_tokens=2000,
                )
                raw = (response.content or "").strip()
                _qa_log(queue, "debug", "LLM 原始响应", raw, "python")

                handler_body = _clean_llm_response(raw)
                _qa_log(queue, "debug", "提取 handler body", handler_body, "python")

                code = QUICK_APP_TEMPLATE.format(
                    name=draft.name, description=draft.description,
                    handler_body=_indent_body(handler_body),
                )
                _qa_log(queue, "info", "最终代码已生成", code, "python")
            except Exception as e:
                last_error = f"LLM 调用失败: {e}"
                _qa_log(queue, "error", "LLM 异常", last_error)
                code = generate_code(draft)
                _qa_log(queue, "warn", "改用 skeleton 兜底", "(LLM 失败，使用模板骨架)")
            _qa_step(queue, "codegen", "done", "代码生成完成", "")
            _tw("CREATE", "代码生成完成", {"name": draft.name, "code_length": len(code), "is_skeleton": "# TODO" in code})

            # ── 3. 构建 schema & venv ──
            schema = build_schema(draft.name, draft.description, draft.params)
            venv_level = draft.venv_level or QuickAppManager.infer_venv_level(source_type)
            _qa_step(queue, "schema", "done", "构建 Schema", f"参数: {[p['name'] for p in draft.params]}")
            _qa_log(queue, "info", "Schema", str(schema.get("function", {}).get("parameters", {})))

            # ── 4. 创建 + 验证 + 注册 ──
            manager = get_manager()
            sample_args = {}
            for p in draft.params:
                ptype = p.get("type", "string").lower()
                sample_args[p["name"]] = 1 if ptype in ("integer","int") else (1.0 if ptype in ("number","float") else (True if ptype == "boolean" else "test"))

            _qa_step(queue, "create", "running", "写入文件", f"写入 .chips/quick_apps/{draft.name}/app.py")
            _qa_log(queue, "info", "样例参数", str(sample_args))

            for attempt in range(3):
                try:
                    if attempt > 0:
                        _qa_step(queue, "codegen", "running", f"重试第 {attempt+1} 次", "重新调用 LLM 生成代码")
                        try:
                            response = agent.gateway.chat(
                                messages=[{"role": "user", "content": prompt}],
                                model=agent.model, max_tokens=2000,
                            )
                            raw = (response.content or "").strip()
                            handler_body = _clean_llm_response(raw)
                            code = QUICK_APP_TEMPLATE.format(
                                name=draft.name, description=draft.description,
                                handler_body=_indent_body(handler_body),
                            )
                        except Exception:
                            code = generate_code(draft)

                    _qa_step(queue, "verify", "running", f"验证 (第 {attempt+1} 次)", "运行样例参数并检查输出")

                    qa = manager.create(
                        name=draft.name, code=code, description=draft.description,
                        schema=schema, source_type=source_type,
                        venv_level=venv_level, sample_args=sample_args,
                        source_name=draft.source_name,
                    )
                    _qa_step(queue, "register", "done", "注册工具", f"已注册到 ToolRegistry（toolset=quick_apps）")
                    _qa_log(queue, "info", "注册完成", f"工具名: {qa.name}, toolset=quick_apps")
                    _tw("CREATE", "创建成功", {"name": qa.name, "source": f"{qa.source_type.value}/{qa.source_name}", "venv_level": qa.venv_level.value})

                    _qa_step(queue, "done", "done", "创建完成", "")
                    queue.put_nowait(json.dumps({
                        "type": "result", "success": True,
                        "name": qa.name,
                        "message": f"已创建快应用「{qa.name}」",
                    }, ensure_ascii=False))
                    return

                except RuntimeError as e:
                    last_error = str(e)
                    _qa_log(queue, "error", f"第 {attempt+1} 次验证失败", last_error)
                    _tw("CREATE", f"验证失败 (attempt {attempt+1})", {"name": draft.name, "error": last_error})
                    continue

            queue.put_nowait(json.dumps({"type": "result", "success": False, "error": f"创建失败（已尝试 3 次）：{last_error}"}, ensure_ascii=False))

        except Exception as e:
            logger.exception("quick_app_create_stream failed")
            queue.put_nowait(json.dumps({"type": "result", "success": False, "error": str(e)}, ensure_ascii=False))
        finally:
            queue.put_nowait(None)

    Thread(target=run, daemon=True).start()

    async def generate():
        while True:
            event = await queue.get()
            if event is None:
                break
            # SSE 格式：data: {...}\n\n
            if isinstance(event, str):
                yield f"data: {event}\n\n"
            else:
                yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ═══════════════════════════════════════════════════════════════
# Token Stats API
# ═══════════════════════════════════════════════════════════════


@app.get("/api/stats/tokens")
def stats_tokens(user: str | None = Depends(optional_user)) -> dict:
    """返回当前会话的 token 用量统计。"""
    try:
        agent = get_agent()
        if hasattr(agent, "gateway") and hasattr(agent.gateway, "summary"):
            return agent.gateway.summary()
    except Exception:
        pass
    return {
        "call_count": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "avg_latency_ms": 0,
    }


# ═══════════════════════════════════════════════════════════════
# QuickApp API
# ═══════════════════════════════════════════════════════════════


@app.post("/api/quick_apps/draft")
def quick_apps_draft(
    body: DraftRequest,
    user: str | None = Depends(optional_user),
) -> dict:
    """根据描述检索方案（known-good → GitHub → PyPI → 兜底），返回草案卡片。"""
    from tool.builtins.quick_app_tools import _handle_draft

    result = _handle_draft({"description": body.description.strip()})
    data = json.loads(result)
    return data


@app.post("/api/quick_apps/create")
def quick_apps_create(
    body: CreateRequest,
    user: str | None = Depends(optional_user),
) -> dict:
    """基于 draft 创建快应用：生成代码 + 验证 + 注册。"""
    draft_data = body.draft

    # ── 校验 draft ──
    required = ["name", "description", "source_type", "source_name"]
    for field in required:
        if field not in draft_data:
            return _qa_error(f"draft 缺少必要字段：{field}")

    from quick_app.models import Draft, SourceType, VenvLevel, build_schema
    from quick_app.manager import get_manager

    try:
        source_type = SourceType(draft_data["source_type"])
    except ValueError:
        return _qa_error(f"不支持的 source_type：{draft_data['source_type']}")

    draft = Draft(
        name=draft_data["name"],
        description=draft_data["description"],
        source_type=source_type,
        source_name=draft_data["source_name"],
        source_reason=draft_data.get("source_reason", ""),
        params=draft_data.get("params", []),
        output_description=draft_data.get("output_description", ""),
        venv_level=VenvLevel(draft_data.get("venv_level", 0)),
        install_hint=draft_data.get("install_hint", ""),
    )

    # ── 双重校验 source ──
    from tool.builtins.quick_app_tools import _check_source_validity
    validation_error = _check_source_validity(draft)
    if validation_error:
        return _qa_error(validation_error)

    # ── LLM 生成代码（带日志） ──
    from tool.builtins.quick_app_tools import _build_llm_code_prompt, _clean_llm_response
    from quick_app.codegen import _indent_body
    from quick_app.models import QUICK_APP_TEMPLATE

    llm_logs = []
    agent = get_agent()

    # 构建 prompt
    prompt = _build_llm_code_prompt(draft)
    llm_logs.append({"step": "构建 prompt", "data": prompt})

    code = ""
    last_error = ""
    try:
        response = agent.gateway.chat(
            messages=[{"role": "user", "content": prompt}],
            model=agent.model,
            max_tokens=2000,
        )
        raw = (response.content or "").strip()
        llm_logs.append({"step": "LLM 原始响应", "data": raw})

        handler_body = _clean_llm_response(raw)
        llm_logs.append({"step": "提取 handler body", "data": handler_body})

        code = QUICK_APP_TEMPLATE.format(
            name=draft.name,
            description=draft.description,
            handler_body=_indent_body(handler_body),
        )
        llm_logs.append({"step": "最终代码", "data": code, "lang": "python"})
    except Exception as e:
        last_error = f"LLM 调用失败: {e}"
        llm_logs.append({"step": "LLM 异常", "data": last_error})
        from quick_app.codegen import generate_code
        code = generate_code(draft)
        llm_logs.append({"step": "改用 skeleton 兜底", "data": "(代码生成失败，使用模板骨架)"})

    # ── 构建 schema & 确定 venv 级别 ──
    schema = build_schema(draft.name, draft.description, draft.params)
    from quick_app.manager import QuickAppManager
    venv_level = draft.venv_level or QuickAppManager.infer_venv_level(source_type)
    llm_logs.append({"step": "Schema", "data": f"参数: {[p['name'] for p in draft.params]}"})

    # ── 创建（写文件 + 验证 + 注册） ──
    manager = get_manager()
    sample_args = _build_qa_sample_args(draft.params)
    llm_logs.append({"step": "样例参数", "data": str(sample_args)})

    for attempt in range(3):
        try:
            if attempt > 0:
                # 重试时重新生成
                try:
                    response = agent.gateway.chat(
                        messages=[{"role": "user", "content": prompt}],
                        model=agent.model,
                        max_tokens=2000,
                    )
                    raw = (response.content or "").strip()
                    handler_body = _clean_llm_response(raw)
                    code = QUICK_APP_TEMPLATE.format(
                        name=draft.name, description=draft.description,
                        handler_body=_indent_body(handler_body),
                    )
                except Exception:
                    from quick_app.codegen import generate_code
                    code = generate_code(draft)

            qa = manager.create(
                name=draft.name,
                code=code,
                description=draft.description,
                schema=schema,
                source_type=source_type,
                venv_level=venv_level,
                sample_args=sample_args,
                source_name=draft.source_name,
            )
            llm_logs.append({"step": "注册完成", "data": f"已注册工具「{qa.name}」"})
            logger.info("quick_app_api_created name=%s", qa.name)
            return {
                "success": True,
                "name": qa.name,
                "message": f"已创建快应用「{qa.name}」",
                "logs": llm_logs,
            }
        except RuntimeError as e:
            last_error = str(e)
            llm_logs.append({"step": f"第 {attempt+1} 次验证失败", "data": last_error})
            logger.warning("quick_app_api_create attempt %d failed: %s", attempt + 1, e)
            continue

    return {
        "success": False,
        "error": f"创建失败（已尝试 3 次）：{last_error}",
        "logs": llm_logs,
    }


@app.get("/api/quick_apps")
def quick_apps_list(
    user: str | None = Depends(optional_user),
) -> list[dict]:
    """列出所有已注册的快应用。"""
    from quick_app.manager import get_manager

    apps = get_manager().list()
    return [
        {
            "name": qa.name,
            "description": qa.description,
            "source_type": qa.source_type.value,
            "venv_level": qa.venv_level.value,
            "created_at": qa.created_at,
        }
        for qa in apps
    ]


@app.get("/api/quick_apps/logs")
def quick_apps_logs(
    name: str | None = Query(default=None, description="按工具名筛选"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: str | None = Depends(optional_user),
) -> list[dict]:
    """返回 QuickApp 执行日志（按时间倒序）。"""
    from quick_app.exec_log import get_logs
    return get_logs(tool_name=name, limit=limit, offset=offset)


@app.delete("/api/quick_apps/{name}")
def quick_apps_delete(
    name: str,
    user: str | None = Depends(optional_user),
) -> dict:
    """删除快应用。"""
    from quick_app.manager import get_manager

    ok = get_manager().delete(name)
    if ok:
        logger.info("quick_app_api_deleted name=%s", name)
        return {"success": True, "message": f"已删除快应用「{name}」"}
    return _qa_error(f"快应用「{name}」不存在")


def _qa_error(message: str) -> dict:
    """统一错误响应格式。"""
    return {"success": False, "error": message}


def _ensure_static_dirs(static_dir: Path) -> None:
    """确保 Starlette StaticFiles(html=True) 能正确服务嵌套路由。

    Starlette v1.2.x 的 html=True 模式不支持 path → path.html 自动降级，
    嵌套路由（如 /quick-apps/create）必须对应目录 + index.html。
    Next.js 静态导出（output: 'export'）会把 /quick-apps/create 生成
    为 quick-apps/create.html，需要补上 quick-apps/create/index.html。
    """
    pairs = [
        # (html 文件路径, 对应的 URL 目录)
        (static_dir / "quick-apps.html", static_dir / "quick-apps"),
        (static_dir / "quick-apps" / "create.html", static_dir / "quick-apps" / "create"),
    ]
    for html_file, target_dir in pairs:
        if html_file.is_file() and not target_dir.is_dir():
            target_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(str(html_file), str(target_dir / "index.html"))
            logger.info("static_fixup created %s/index.html from %s", target_dir, html_file.name)


def _build_qa_sample_args(params: list[dict]) -> dict[str, Any]:
    """从参数列表构建样例参数字段（同 quick_app_tools 中的逻辑）。"""
    samples = {}
    for p in params:
        pname = p["name"]
        ptype = p.get("type", "string").lower()
        if ptype in ("integer", "int"):
            samples[pname] = 1
        elif ptype in ("number", "float"):
            samples[pname] = 1.0
        elif ptype == "boolean":
            samples[pname] = True
        elif ptype == "array":
            samples[pname] = []
        elif ptype == "object":
            samples[pname] = {}
        else:
            samples[pname] = "test"
    return samples


# ── 静态文件 ──

if STATIC_DIR.is_dir():
    # Starlette StaticFiles(html=True) 不支持 path → path.html 自动降级，
    # 所以嵌套路由（如 /quick-apps/create）必须用目录 + index.html 形式。
    # pnpm build 输出的是 create.html，需要在启动时补上目录结构。
    _ensure_static_dirs(STATIC_DIR)
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def run(host: str = "0.0.0.0", port: int = 8648):
    import uvicorn

    from agent.logger import setup_logging
    setup_logging(console=True)
    logger.info("chips web starting on http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
