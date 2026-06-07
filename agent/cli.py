"""chips CLI 入口"""

import argparse
import os

from dotenv import load_dotenv

from agent.loop import AIAgent
from agent.logger import setup_logging, get_logger
from agent.prompt import search_context_files
from config.store import ConfigStore
from memory.manager import MemoryManager
from memory.providers.builtin import BuiltinMemoryProvider
from session.db import SessionDB
from tool.registry import registry
from tool.toolsets import resolve_toolset

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
    if args.command == "config":
        from config.cli import handle_config
        handle_config(args)
        return
    if args.command == "session":
        from session.cli import handle_session
        handle_session(args)
        return

    if args.version:
        print("chips 0.3.0")
        return

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("错误: 未设置 DEEPSEEK_API_KEY")
        print("请在 .env 文件中配置: DEEPSEEK_API_KEY=sk-...")
        return

    agent = AIAgent(api_key=api_key, base_url=args.base_url, model=args.model, debug_context=args.debug_context, verbose=args.verbose, stream=not args.no_stream)
    # 临时手动 wiring，后续阶段会改为构造注入
    agent.registry = registry
    agent.tool_names = resolve_toolset(args.toolset) & registry.tool_names

    # 在 CWD 搜索上下文文件并注入 agent
    context_files = search_context_files()
    if context_files:
        agent.context_files = context_files

    if not args.no_memory:
        agent.memory_manager = _build_memory_manager(holographic=args.holographic)

    # ── 环境层初始化（terminal_tool 自己读 CHIPS_ENV 懒加载） ──
    os.environ["CHIPS_ENV"] = args.env
    if args.env == "docker":
        os.environ["CHIPS_DOCKER_IMAGE"] = args.docker_image

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

    # ── 日志初始化 ──
    setup_logging(session_id=agent.session_id)
    get_logger().info("session started")

    if args.message:
        reply = agent.run_conversation(args.message)
        if reply:
            print(reply)
        return

    ctx_count = len(agent.context_files)
    mem_status = "off"
    if agent.memory_manager.providers:
        provider_names = [p.name for p in agent.memory_manager.providers]
        mem_status = "+".join(provider_names)
    print(f"chips v0.3.0 — model: {args.model}  base_url: {args.base_url}")
    print(f"工具集: {args.toolset}  |  已加载工具: {len(agent.tool_names)}  |  记忆: {mem_status}  |  上下文文件: {ctx_count}")
    print("输入 /help 查看命令, /exit 退出")

    from agent.repl import ReplLoop, CommandRegistry, StdioOutputBackend
    from agent.repl_prompt_toolkit import PromptToolkitInputBackend

    cmd_reg = CommandRegistry()

    loop = ReplLoop(
        agent=agent,
        input_backend=PromptToolkitInputBackend(commands=cmd_reg.command_names),
        output_backend=StdioOutputBackend(),
        cmd_registry=cmd_reg,
    )
    loop.run()


if __name__ == "__main__":
    main()
