"""boot — Agent 系统组装与启动

接收 cli.py 解析后的 args，完成所有 wiring 并启动 ReplLoop。

这是 chips 整个系统结构的"工作目录"——
阅读此文件即可了解所有组件如何连接。
"""

from __future__ import annotations

import os
import signal
import sys
import time

from agent.logger import setup_logging, get_logger
from agent.loop import AIAgent
from agent.prompt import search_context_files
from agent.repl import ReplLoop, CommandRegistry, StdioOutputBackend
from agent.repl_prompt_toolkit import PromptToolkitInputBackend
from config.agent_config import AgentRegistry
from config.store import ConfigStore
from gateway.providers.openai import OpenAIProvider
from gateway.stats import UsageRecorder
from memory.manager import MemoryManager
from memory.providers.builtin import BuiltinMemoryProvider
from plugins import PluginManager
from plugins.mcp import MCPManager
from session.db import SessionDB
import tool.builtins  # noqa: F401 — 导入即注册所有工具
from tool.registry import registry


def _wire_tools(agent):
    """接线 agent registry / todo store。返回 AgentRegistry 实例。"""
    from tool.builtins.agent_tools import wire_parent, wire_registry
    wire_parent(agent)

    from config.agent_config import AgentRegistry
    agent_registry = AgentRegistry()
    if agent_registry:
        wire_registry(agent_registry)
        _inject_agent_schemas(agent_registry)

    from tool.builtins.todo_tool import TodoStore, wire_store as wire_todo_store
    wire_todo_store(TodoStore())

    from tool.builtins.deferred_tools import wire_agent as wire_deferred_agent
    wire_deferred_agent(agent)

    return agent_registry


def _register_slash_commands(agent, agent_registry):
    """注册所有 /slash 命令，返回 CommandRegistry。"""
    from agent.repl import CommandRegistry
    cmd_reg = CommandRegistry()

    def _clear(args):
        agent.messages.clear()
        agent._saved_count = 0
        agent._tool_call_history.clear()
        if agent.context_engine:
            agent.context_engine.on_session_reset()
        return u"✅ 对话历史已清除"
    cmd_reg.register("clear", _clear, u"清除当前对话历史")

    def _compact(args):
        if not agent.context_engine:
            return u"⚠ 未启用上下文压缩引擎（启动时加 --no-compress 了吗？）"
        if len(agent.messages) < 4:
            return u"对话太短，无需压缩"
        before = len(agent.messages)
        before_chars = sum(len(m.get("content", "") or "") for m in agent.messages)
        agent.messages = agent.context_engine.compress(agent.messages)
        after = len(agent.messages)
        after_chars = sum(len(m.get("content", "") or "") for m in agent.messages)
        return f"✅ 已压缩：{before} → {after} 条消息（{before_chars} → {after_chars} 字符）"
    cmd_reg.register("compact", _compact, u"手动压缩对话上下文（减少 token 占用）")

    from agent.commands.rewind_cmd import make_handler as _make_rewind_handler
    cmd_reg.register("rewind", _make_rewind_handler(agent), u"回退 N 轮对话（如 /rewind 3）")

    from agent.commands.agent_cmd import make_handler as _make_agent_handler
    cmd_reg.register("agent", _make_agent_handler(agent_registry),
                     u"Agent 角色管理：/agent list | add <name> --tools ... | remove <name> | update <name> --model ...")

    return cmd_reg


def run(args: object) -> None:
    """组装所有组件并启动交互式对话。

    Args:
        args: argparse 解析后的命名空间（由 cli.py 传入）
    """
    # ── 1. 核心基础设施 ──
    pricing = ConfigStore().read_pricing()

    # ── 模型后端（主用 + 备用） ──
    _backends = ConfigStore().read_model_backends()
    # 命令行 --fallback-model 追加一个备用模型
    fb_model = getattr(args, "fallback_model", None)
    if fb_model:
        fb_key = os.getenv("CHIPS_FALLBACK_API_KEY") or _backends[0].get("api_key", "")
        fb_base = getattr(args, "fallback_base_url", "") or ""
        _backends = [b for b in _backends if b["model"] != fb_model]
        _backends.append({
            "model": fb_model, "base_url": fb_base, "api_key": fb_key or "",
        })

    # 构建 Gateway 链
    if len(_backends) > 1:
        from gateway.fallback import FallbackGateway
        chain: list[ModelGateway] = []
        for b in _backends:
            chain.append(OpenAIProvider(
                api_key=b.get("api_key", ""),
                base_url=b.get("base_url", ""),
            ))
        _models = [b["model"] for b in _backends]
        fallback_gw = FallbackGateway(list(zip(_models, chain)))
        recorder = UsageRecorder(fallback_gw, pricing=pricing)
        get_logger().info(
            "fallback_gateway_enabled primary=%s fallbacks=%s",
            _models[0], _models[1:],
        )
        _active_model = _backends[0]["model"]
    else:
        b0 = _backends[0]
        raw_gateway = OpenAIProvider(
            api_key=b0.get("api_key", ""),
            base_url=b0.get("base_url", ""),
        )
        recorder = UsageRecorder(raw_gateway, pricing=pricing)
        _active_model = b0["model"]

    agent = AIAgent(
        model=_active_model,
        debug_context=args.debug_context,
        verbose=args.verbose,
        stream=not args.no_stream,
        gateway=recorder,
    )
    agent.registry = registry

    # 端侧模型 gateway（Ollama OpenAI 兼容接口）
    agent._ollama_gateway = OpenAIProvider(
        api_key="ollama",
        base_url="http://localhost:11434/v1",
    )
    toolset_names = [n.strip() for n in args.toolset.split(",")]
    agent._resolve_tool_names()

    context_files = search_context_files()
    if context_files:
        agent.context_files = context_files

    if not args.no_memory:
        agent.memory_manager = _build_memory_manager(holographic=args.holographic)

    # ── 2. 插件 + MCP ──
    plugin_mgr = PluginManager(registry=registry)
    plugin_mgr.add_default_paths()
    loaded = plugin_mgr.load_all()
    if loaded:
        get_logger().info("plugins_loaded count=%d", loaded)
    agent.plugin_manager = plugin_mgr
    agent.tool_names |= plugin_mgr.plugin_tool_names
    agent._extra_tool_names |= plugin_mgr.plugin_tool_names

    mcp_mgr = MCPManager(registry=registry)
    mcp_servers_config = ConfigStore().read_mcp_servers()
    mcp_loaded = []
    if mcp_servers_config:
        mcp_loaded = mcp_mgr.load_servers(mcp_servers_config)
        agent.tool_names |= set(mcp_mgr.get_all_tool_names())
        agent._extra_tool_names |= set(mcp_mgr.get_all_tool_names())
    agent.mcp_manager = mcp_mgr

    # ── 3. 工具系统接线 ──
    agent_registry = _wire_tools(agent)
    _wire_guard_engine(agent)
    _wire_fast_llm(agent)
    _wire_sub_agent_hooks(agent)

    # ── 4. 执行环境 ──
    os.environ["CHIPS_ENV"] = args.env
    if args.env == "docker":
        os.environ["CHIPS_DOCKER_IMAGE"] = args.docker_image

    # ── 5. 上下文压缩引擎 ──
    if not args.no_compress:
        from agent.context_compressor import ContextCompressor
        compressor = ContextCompressor(
            threshold_percent=0.50,
            summarize_fn=lambda prompt: agent.gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                model=agent.model,
                max_tokens=4000,
            ).content or "",
        )
        compressor.update_context_length(128_000)
        agent.context_engine = compressor

    # ── 6. Session 持久化 ──
    session_db = SessionDB(db_path=".chips/sessions.db")
    agent.session_db = session_db
    _restore_or_create_session(agent, args)
    recorder._session_db = session_db
    recorder._session_id = agent.session_id

    # 子 Agent 持久化：绑定 session 后恢复历史记录
    agent._sub_agent_manager.set_session(session_db, agent.session_id)
    restored = agent._sub_agent_manager.restore(agent.session_id)
    if restored:
        get_logger().info("sub_agent_history_restored count=%d", restored)

    # 启动子 Agent 超时清理线程
    agent._sub_agent_manager.start_cleaner()

    # ── 7. 日志 + TUI + 技能系统 ──
    setup_logging(session_id=agent.session_id)
    get_logger().info("session started")

    from agent.tui import TUI
    tui = TUI()

    from agent.skill import SkillManager
    from tool.builtins.skill_tools import wire_skill_manager, wire_plugin_manager
    skill_mgr = SkillManager()
    skill_mgr.scan()
    agent.skills_index = skill_mgr.get_skills_index_prompt()
    wire_skill_manager(skill_mgr)
    wire_plugin_manager(plugin_mgr)

    # ── 8. 斜杠命令注册 ──
    cmd_reg = _register_slash_commands(agent, agent_registry)

    # ── 9. 启动画面 ──
    mem_status = "off"
    if agent.memory_manager.providers:
        provider_names = [p.name for p in agent.memory_manager.providers]
        mem_status = "+".join(provider_names)
    mcp_status = f"{len(mcp_loaded)} servers ({mcp_mgr.tool_count} tools)" if mcp_loaded else "off"
    skill_status = f"{skill_mgr.count} skills" if skill_mgr.count else "off"
    compress_status = "off" if args.no_compress else "on"

    # 降级链信息
    _fb_count = len(_backends) - 1 if len(_backends) > 1 else 0
    _model_display = agent.model
    if _fb_count:
        _model_display += f" + {_fb_count} backup"

    tui.startup(
        model=_model_display,
        tool_count=len(agent.tool_names),
        toolset_names=toolset_names,
        memory_status=mem_status,
        mcp_status=mcp_status,
        skill_status=skill_status,
        compress_status=compress_status,
        context_file_count=len(agent.context_files),
    )

    # ── 单条消息模式 ──
    if args.message:
        tui.chat(agent, args.message)
        stats = recorder.format_summary()
        if stats:
            print(f"\n{stats}")
        return

    print("输入 /help 查看命令, /exit 退出")
    print()

    # ── 10. SIGINT 处理器 ──
    _last_sigint = 0.0

    def _sigint_handler(signum, frame):
        nonlocal _last_sigint
        now = time.time()
        if now - _last_sigint < 2.0:
            print("\n[强制退出]")
            sys.exit(1)
        _last_sigint = now
        agent.interrupt()
        print("\n[正在中断...]")

    signal.signal(signal.SIGINT, _sigint_handler)

    # ── 11. 启动 ReplLoop ──
    loop = ReplLoop(
        agent=agent,
        input_backend=PromptToolkitInputBackend(commands=cmd_reg.command_names),
        output_backend=StdioOutputBackend(),
        cmd_registry=cmd_reg,
        tui=tui,
    )
    loop.run()
    stats = recorder.format_summary()
    if stats:
        print(f"\n{stats}")
    agent.shutdown()


# ── 辅助函数 ──


def _build_memory_manager(holographic: bool = False) -> MemoryManager:
    memory_dir = os.getenv("CHIPS_MEMORY_DIR", ".memory")
    mm = MemoryManager()
    mm.add_provider(BuiltinMemoryProvider(memory_dir=memory_dir))
    if holographic:
        from memory.providers.holographic import HolographicMemoryProvider
        mm.add_provider(HolographicMemoryProvider())
    return mm


def _inject_agent_schemas(agent_registry: AgentRegistry) -> None:
    """将 agent 角色名注入 orchestrate 的 schema enum。

    delegate_task/decompose 已合并到 orchestrate 中，不再单独注册。
    """
    _agent_names = agent_registry.names
    _orch = registry._entries.get("orchestrate")
    if _orch and _agent_names:
        _os = _orch.schema
        # agent 参数（single/decompose 模式用）
        _os["function"]["parameters"]["properties"]["agent"]["enum"] = _agent_names
        # steps[].agent（supervisor/pipeline 模式用）
        _os["function"]["parameters"]["properties"]["steps"]["items"]["properties"]["agent"]["enum"] = _agent_names
        # agents[]（debate 模式用）
        _os["function"]["parameters"]["properties"]["agents"]["items"]["enum"] = _agent_names
    get_logger().info("agent_registry loaded names=%s injected into orchestrate schema", _agent_names)


def _restore_or_create_session(agent: AIAgent, args: object) -> None:
    """恢复已有会话或创建新会话。"""
    session_db = agent.session_db
    if args.resume:
        session_id = args.resume if isinstance(args.resume, str) else None
        if not session_id:
            sessions = session_db.list_sessions(limit=1)
            if sessions:
                session_id = sessions[0]["id"]
        if session_id:
            sess = session_db.get_session(session_id)
            if sess:
                agent.session_id = session_id
                agent.messages = session_db.get_history(session_id)
                agent._saved_count = len(agent.messages)
                print(f"已恢复会话 {session_id}（{len(agent.messages)} 条消息）")
    if not agent.session_id:
        agent.session_id = session_db.create_session()


def _wire_guard_engine(agent):
    """将 GuardEngine 注入 Agent — 安全拦截层。

    始终启用（不依赖命令行参数），只做 block / direct 判断。
    委托/编排已移除——由主 LLM 在 ReAct 循环内自行决定调 delegate_task 或 decompose。
    """
    from safety.guard import GuardEngine
    guard = GuardEngine(include_defaults=True)
    agent._guard_engine = guard
    get_logger().info("guard_engine_loaded rules=%d", len(guard.rules))


def _wire_fast_llm(agent):
    """将 FastLLM 注入 Agent — 端侧小模型快速通道。

    启动时检测 Ollama 可用性，不可用时静默降级。
    只做"小模型能不能答"的判断，不做路由分发。
    """
    from endpoint.fast_llm import FastLLM
    agent._fast_llm = FastLLM()
    get_logger().info("fast_llm_initialized")


def _wire_sub_agent_hooks(agent):
    """注册子 Agent 生命周期钩子 → Prometheus 指标。

    不阻塞：钩子异常不影响主流程。
    """
    mgr = agent._sub_agent_manager

    try:
        from gateway.metrics import (
            agent_calls_total,
            agent_duration_seconds,
            agent_tokens_total,
            agent_concurrent,
        )

        # 并发计数
        _running: dict[str, int] = {}

        def _on_created(record):
            # 预置并发计数为 0（确保 Gauge 有 label）
            name = record.agent_name
            if name not in _running:
                _running[name] = 0
                agent_concurrent.labels(agent_name=name).set(0)

        def _on_running(record):
            name = record.agent_name
            _running[name] = _running.get(name, 0) + 1
            agent_concurrent.labels(agent_name=name).set(_running[name])

        def _on_completed(record):
            name = record.agent_name
            _running[name] = max(0, _running.get(name, 0) - 1)
            agent_concurrent.labels(agent_name=name).set(_running.get(name, 0))
            agent_calls_total.labels(agent_name=name, status="completed").inc()
            if record.started_at and record.completed_at:
                duration = record.completed_at - record.started_at
                agent_duration_seconds.labels(agent_name=name).observe(duration)
            agent_tokens_total.labels(agent_name=name, token_type="prompt").inc(record.prompt_tokens)
            agent_tokens_total.labels(agent_name=name, token_type="completion").inc(record.completion_tokens)

        def _on_failed(record):
            name = record.agent_name
            _running[name] = max(0, _running.get(name, 0) - 1)
            agent_concurrent.labels(agent_name=name).set(_running.get(name, 0))
            agent_calls_total.labels(agent_name=name, status="failed").inc()

        def _on_cancelled(record):
            name = record.agent_name
            _running[name] = max(0, _running.get(name, 0) - 1)
            agent_concurrent.labels(agent_name=name).set(_running.get(name, 0))

        mgr.on("created", _on_created)
        mgr.on("running", _on_running)
        mgr.on("completed", _on_completed)
        mgr.on("failed", _on_failed)
        mgr.on("cancelled", _on_cancelled)

        get_logger().info("sub_agent_hooks_registered")
    except ImportError:
        get_logger().info("sub_agent_hooks_skipped: prometheus_client not available")
    except Exception as exc:
        get_logger().warning("sub_agent_hooks_error: %s", exc)
