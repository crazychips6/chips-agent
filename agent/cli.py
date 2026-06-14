"""chips CLI 入口"""

import argparse
import os
import signal
import sys
import time

from dotenv import load_dotenv

from agent.loop import AIAgent
from agent.logger import setup_logging, get_logger
from agent.prompt import search_context_files
from config.store import ConfigStore
from gateway.providers.openai import OpenAIProvider
from gateway.stats import UsageRecorder
from memory.manager import MemoryManager
from memory.providers.builtin import BuiltinMemoryProvider
from plugins import PluginManager
from plugins.mcp import MCPManager
from session.db import SessionDB
from tool.registry import registry
from tool.toolsets import CORE_ALWAYS_ON, resolve_multiple_toolsets

# 模块级 side-effect import：触发 builtins 目录下各工具的 registry.register() 自注册
import tool.builtins  # noqa: F401


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chips6",
        description="A general-purpose agent harness",
    )
    parser.add_argument("--model", default=os.getenv("CHIPS_MODEL", "deepseek-chat"))
    parser.add_argument("--base-url", default=os.getenv("CHIPS_BASE_URL", "https://api.deepseek.com"))
    parser.add_argument("--message", "-m", help="Single message and exit")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--debug-context", action="store_true", help="将每轮 LLM 请求/响应写入 log/debug/session.json")
    parser.add_argument("--toolset", default="core", help="使用的工具集，默认 core")
    parser.add_argument("--no-memory", action="store_true", help="禁用记忆系统")
    parser.add_argument("--holographic", action="store_true", help="启用 Holographic 记忆（SQLite 事实存储 + 语义检索）")
    parser.add_argument("--verbose", action="store_true", help="显示 system prompt 各层详情")
    parser.add_argument("--resume", nargs="?", const=True, default=False,
                        help="恢复上次会话，或指定 session_id 恢复特定会话")
    parser.add_argument("--no-stream", action="store_true", help="禁用 streaming 输出")
    parser.add_argument("--env", default="local", choices=["local", "docker"],
                        help="执行环境: local（本地）或 docker（容器沙盒）")
    parser.add_argument("--docker-image", default="alpine:latest",
                        help="Docker 环境使用的镜像名（仅在 --env=docker 时生效）")
    parser.add_argument("--no-compress", action="store_true",
                        help="禁用上下文压缩")

    # 子命令：chips config set/get/list
    subparsers = parser.add_subparsers(dest="command")
    config_cmd = subparsers.add_parser("config", help="管理配置")
    config_sub = config_cmd.add_subparsers(dest="config_action", required=True)
    config_sub.add_parser("list", help="列出所有配置")
    config_get = config_sub.add_parser("get", help="获取配置值")
    config_get.add_argument("key", help="配置键名")
    config_set = config_sub.add_parser("set", help="设置配置值")
    config_set.add_argument("key", help="配置键名")
    config_set.add_argument("value", help="配置值")

    # 子命令：chips plugin list/install/remove/info
    plugin_cmd = subparsers.add_parser("plugin", help="管理插件")
    plugin_sub = plugin_cmd.add_subparsers(dest="plugin_action", required=True)
    plugin_sub.add_parser("list", help="列出已安装插件")
    plugin_install = plugin_sub.add_parser("install", help="安装插件（复制 .py 到 ~/.chips/plugins/）")
    plugin_install.add_argument("path_or_package", help="插件文件路径")
    plugin_remove = plugin_sub.add_parser("remove", help="卸载插件")
    plugin_remove.add_argument("name", help="插件名（不含 .py）")
    plugin_info = plugin_sub.add_parser("info", help="查看插件详情")
    plugin_info.add_argument("name", help="插件名（不含 .py）")

    # 子命令：chips session list/show/search/delete
    session_cmd = subparsers.add_parser("session", help="管理会话")
    session_sub = session_cmd.add_subparsers(dest="session_action", required=True)
    session_sub.add_parser("list", help="列出最近会话")
    session_show = session_sub.add_parser("show", help="显示会话详情")
    session_show.add_argument("session_id", help="会话 ID")
    session_search = session_sub.add_parser("search", help="全文搜索消息")
    session_search.add_argument("query", help="搜索关键词")
    session_delete = session_sub.add_parser("delete", help="删除会话")
    session_delete.add_argument("session_id", help="会话 ID")
    session_stats = session_sub.add_parser("stats", help="显示会话用量统计")
    session_stats.add_argument("session_id", help="会话 ID")

    # 子命令：chips web
    web_cmd = subparsers.add_parser("web", help="启动 Web 聊天界面")
    web_cmd.add_argument("--host", default="0.0.0.0", help="监听地址")
    web_cmd.add_argument("--port", type=int, default=8648, help="监听端口")

    return parser


def _build_memory_manager(holographic: bool = False) -> MemoryManager:
    """构建记忆子系统（MemoryManager + BuiltinMemoryProvider + 可选 Holographic）。"""
    memory_dir = os.getenv("CHIPS_MEMORY_DIR", ".memory")
    mm = MemoryManager()
    mm.add_provider(BuiltinMemoryProvider(memory_dir=memory_dir))
    if holographic:
        from memory.providers.holographic import HolographicMemoryProvider
        mm.add_provider(HolographicMemoryProvider())
    return mm


def main():
    load_dotenv()

    # 加载 ~/.chips/config.yaml，仅当对应环境变量未设置时生效
    ConfigStore().apply_to_env()

    parser = _build_parser()
    args = parser.parse_args()

    # ── 子命令处理 ──
    if args.command == "web":
        from web.server import run as run_web
        run_web(host=args.host, port=args.port)
        return
    if args.command == "config":
        from config.cli import handle_config
        handle_config(args)
        return
    if args.command == "session":
        from session.cli import handle_session
        handle_session(args)
        return
    if args.command == "plugin":
        from plugins.cli import handle_plugin
        handle_plugin(args)
        return

    if args.version:
        print("chips 0.3.0")
        return

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("错误: 未设置 DEEPSEEK_API_KEY")
        print("请在 .env 文件中配置: DEEPSEEK_API_KEY=sk-...")
        return

    # 构造 gateway + 用量记录器，注入 agent
    raw_gateway = OpenAIProvider(api_key=api_key, base_url=args.base_url)
    recorder = UsageRecorder(raw_gateway, pricing=ConfigStore().read_pricing())
    agent = AIAgent(model=args.model, debug_context=args.debug_context, verbose=args.verbose, stream=not args.no_stream, gateway=recorder)
    # 临时手动 wiring，后续阶段会改为构造注入
    agent.registry = registry
    toolset_names = [n.strip() for n in args.toolset.split(",")]
    # permanent_toolsets：--toolset 指定的常驻 toolset（不受 hot zone 影响）
    agent.permanent_toolsets = list(toolset_names)
    # 初始 tool_names = CORE_ALWAYS_ON ∪ permanent（hot zone 起始为空）
    agent.tool_names = (CORE_ALWAYS_ON | set(resolve_multiple_toolsets(toolset_names))) & registry.tool_names

    # 在 CWD 搜索上下文文件并注入 agent
    context_files = search_context_files()
    if context_files:
        agent.context_files = context_files

    if not args.no_memory:
        agent.memory_manager = _build_memory_manager(holographic=args.holographic)

    # ── 插件系统初始化 ──
    plugin_mgr = PluginManager(registry=registry)
    plugin_mgr.add_default_paths()
    loaded = plugin_mgr.load_all()
    if loaded:
        get_logger().info("plugins_loaded count=%d", loaded)
    agent.plugin_manager = plugin_mgr
    # 插件注册的工具需要额外加入 agent 可用工具列表（不受 toolset 开关影响）
    agent.tool_names |= plugin_mgr.plugin_tool_names
    agent._extra_tool_names |= plugin_mgr.plugin_tool_names

    # ── MCP 服务器初始化（从 config.yaml 读取配置） ──
    mcp_mgr = MCPManager(registry=registry)
    mcp_servers_config = ConfigStore().read_mcp_servers()
    mcp_loaded = []
    if mcp_servers_config:
        mcp_loaded = mcp_mgr.load_servers(mcp_servers_config)
        # MCP 注册的工具也加入 agent 可用工具列表
        agent.tool_names |= set(mcp_mgr.get_all_tool_names())
        agent._extra_tool_names |= set(mcp_mgr.get_all_tool_names())
    agent.mcp_manager = mcp_mgr

    # ── toolset 工具接线（动态开关工具集） ──
    from tool.builtins.toolset_tool import wire_agent as wire_toolset_agent
    wire_toolset_agent(agent)

    # ── delegate_task 工具接线（子 Agent 委派 + AgentRegistry） ──
    from tool.builtins.agent_tools import wire_parent, wire_registry
    wire_parent(agent)
    from config.agent_config import AgentRegistry
    agent_registry = AgentRegistry()
    if agent_registry:
        wire_registry(agent_registry)
        # ── 将 agent 角色名注入工具 schema（enum 约束，LLM 第一轮就能选对） ──
        from tool.registry import registry as _tool_registry
        _agent_names = agent_registry.names
        # delegate_task: agent 参数增加 enum 约束
        _de = _tool_registry._entries["delegate_task"].schema
        _de["function"]["parameters"]["properties"]["agent"]["enum"] = _agent_names
        # orchestrate: steps[].agent + debate agents 数组
        _orch = _tool_registry._entries.get("orchestrate")
        if _orch:
            _os = _orch.schema
            _os["function"]["parameters"]["properties"]["steps"]["items"]["properties"]["agent"]["enum"] = _agent_names
            _os["function"]["parameters"]["properties"]["agents"]["items"]["enum"] = _agent_names
        get_logger().info("agent_registry loaded names=%s injected into delegate_task/orchestrate schema", _agent_names)

    # ── TodoStore（模块级，供 todo 工具使用） ──
    from tool.builtins.todo_tool import TodoStore, wire_store as wire_todo_store
    wire_todo_store(TodoStore())

    # ── 环境层初始化（terminal_tool 自己读 CHIPS_ENV 懒加载） ──
    os.environ["CHIPS_ENV"] = args.env
    if args.env == "docker":
        os.environ["CHIPS_DOCKER_IMAGE"] = args.docker_image

    # ── 上下文压缩引擎 ──
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

    # ── Session 持久化 ──
    session_db = SessionDB(db_path=".chips/sessions.db")
    agent.session_db = session_db

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

    # 用量记录器绑定会话
    recorder._session_db = session_db
    recorder._session_id = agent.session_id

    # ── TUI 初始化 ──
    from agent.tui import TUI
    tui = TUI()

    # ── 日志初始化 ──
    setup_logging(session_id=agent.session_id)
    get_logger().info("session started")

    # ── 技能系统（REPL 模式） ──
    from agent.skill import SkillManager
    from tool.builtins.skill_tools import wire_skill_manager, wire_plugin_manager

    skill_mgr = SkillManager()
    skill_mgr.scan()
    agent.skills_index = skill_mgr.get_skills_index_prompt()
    wire_skill_manager(skill_mgr)
    wire_plugin_manager(plugin_mgr)

    mem_status = "off"
    if agent.memory_manager.providers:
        provider_names = [p.name for p in agent.memory_manager.providers]
        mem_status = "+".join(provider_names)
    mcp_status = f"{len(mcp_loaded)} servers ({mcp_mgr.tool_count} tools)" if mcp_loaded else "off"
    skill_status = f"{skill_mgr.count} skills" if skill_mgr.count else "off"
    compress_status = "off" if args.no_compress else "on"

    tui.startup(
        model=args.model,
        tool_count=len(agent.tool_names),
        toolset_names=toolset_names,
        memory_status=mem_status,
        mcp_status=mcp_status,
        skill_status=skill_status,
        compress_status=compress_status,
        context_file_count=len(agent.context_files),
    )

    if args.message:
        tui.chat(agent, args.message)
        stats = recorder.format_summary()
        if stats:
            print(f"\n{stats}")
        return

    print("输入 /help 查看命令, /exit 退出")
    print()  # 首条消息前空一行

    from agent.repl import ReplLoop, CommandRegistry, StdioOutputBackend
    from agent.repl_prompt_toolkit import PromptToolkitInputBackend

    cmd_reg = CommandRegistry()

    # ── SIGINT 处理器（双击 Ctrl+C 强制退出） ──
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

    # ── /clear — 清除对话历史 ──
    def _cmd_clear(args: list[str]) -> str | None:
        agent.messages.clear()
        agent._saved_count = 0
        agent._tool_call_history.clear()
        if agent.context_engine:
            agent.context_engine.on_session_reset()
        return "✅ 对话历史已清除"
    cmd_reg.register("clear", _cmd_clear, "清除当前对话历史")

    # ── /compact — 手动压缩对话上下文 ──
    def _cmd_compact(args: list[str]) -> str | None:
        if not agent.context_engine:
            return "⚠ 未启用上下文压缩引擎（启动时加 --no-compress 了吗？）"
        if len(agent.messages) < 4:
            return "对话太短，无需压缩"
        before = len(agent.messages)
        before_chars = sum(len(m.get("content", "") or "") for m in agent.messages)
        agent.messages = agent.context_engine.compress(agent.messages)
        after = len(agent.messages)
        after_chars = sum(len(m.get("content", "") or "") for m in agent.messages)
        return f"✅ 已压缩：{before} → {after} 条消息（{before_chars} → {after_chars} 字符）"
    cmd_reg.register("compact", _cmd_compact, "手动压缩对话上下文（减少 token 占用）")

    # ── /rewind — 回退 N 轮对话 ──
    def _cmd_rewind(args: list[str]) -> str | None:
        """将对话回退到之前的状态。"""
        n = 1
        if args:
            try:
                n = int(args[0])
                if n < 1:
                    return "⚠ 回退轮数必须 >= 1"
            except ValueError:
                return f"⚠ 无效参数：{args[0]}，用法：/rewind [轮数]"

        if not agent.messages:
            return "对话已为空，无法回退"

        # 从尾部向前找第 N 个 user 消息的索引
        user_indices = [i for i, m in enumerate(agent.messages) if m.get("role") == "user"]
        if len(user_indices) <= 1:
            return "⚠ 历史不足，无法回退（至少需要保留一个 user 消息）"
        if n >= len(user_indices):
            return f"⚠ 对话历史不足（当前共 {len(user_indices) - 1} 轮可回退）"

        cut_at = user_indices[-n]
        removed = len(agent.messages) - cut_at

        # 截断消息（保留 cut_at 之前的内容）
        agent.messages = agent.messages[:cut_at]
        agent._saved_count = len(agent.messages)
        agent._tool_call_history.clear()

        # 重置上下文压缩计数器
        if agent.context_engine:
            agent.context_engine.on_session_reset()

        return (
            f"✅ 已回退 {n} 轮（移除 {removed} 条消息）\n"
            f"⚠ 仅恢复消息历史，已执行的工具操作不会被撤销"
        )
    cmd_reg.register("rewind", _cmd_rewind, "回退 N 轮对话（如 /rewind 3）")

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


if __name__ == "__main__":
    main()
