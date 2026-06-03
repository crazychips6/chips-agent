"""chips CLI 入口"""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from agent.loop import AIAgent
from agent.logger import setup_logging, get_logger
from agent.prompt import search_context_files
from memory.store import MemoryStore
from session.db import SessionDB
from tool.registry import registry
from tool.toolsets import resolve_toolset

# 模块级 side-effect import：触发 builtins 目录下各工具的 registry.register() 自注册
import tool.builtins  # noqa: F401

_CONFIG_PATH = Path.home() / ".chips" / "config.yaml"


def _load_config() -> dict:
    """加载 ~/.chips/config.yaml，不存在时返回空字典。"""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        with open(_CONFIG_PATH) as f:
            cfg: dict = yaml.safe_load(f) or {}
        return cfg
    except Exception:
        return {}


def main():
    load_dotenv()

    # 加载 ~/.chips/config.yaml，仅当对应环境变量未设置时生效
    cfg = _load_config()
    for key, env_name in [("model", "CHIPS_MODEL"), ("base_url", "CHIPS_BASE_URL")]:
        if key in cfg and not os.getenv(env_name):
            os.environ[env_name] = str(cfg[key])

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
    parser.add_argument("--verbose", action="store_true", help="显示 system prompt 各层详情")
    parser.add_argument("--resume", nargs="?", const=True, default=False,
                        help="恢复上次会话，或指定 session_id 恢复特定会话")
    parser.add_argument("--no-stream", action="store_true", help="禁用 streaming 输出")
    parser.add_argument("--env", default="local", choices=["local", "docker"],
                        help="执行环境: local（本地）或 docker（容器沙盒）")
    parser.add_argument("--docker-image", default="alpine:latest",
                        help="Docker 环境使用的镜像名（仅在 --env=docker 时生效）")
    args = parser.parse_args()

    if args.version:
        print("chips 0.2.0")
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
        import tool.builtins.memory as memory_tool

        # 合并 memory 工具集，默认启用记忆
        agent.tool_names |= resolve_toolset("memory") & registry.tool_names

        memory_store = MemoryStore(memory_dir=".memory")
        agent.memory = memory_store
        memory_tool._store = memory_store

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

    memory_snapshot = agent.memory.for_system_prompt() if agent.memory else ""
    memory_lines = len([l for l in memory_snapshot.split("\n") if l.strip()]) if memory_snapshot else 0
    ctx_count = len(agent.context_files)
    print(f"chips v0.1.0 — model: {args.model}  base_url: {args.base_url}")
    print(f"工具集: {args.toolset}  |  已加载工具: {len(agent.tool_names)}  |  记忆: {memory_lines} 行  |  上下文文件: {ctx_count}")
    print("输入 /help 查看命令, /exit 退出")

    # 交互式 REPL：每次输入触发一次 LLM 对话
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not text:
            continue
        if text == "/exit":
            break
        if text == "/help":
            print("命令: /exit 退出  /help 帮助")
            continue

        reply = agent.run_conversation(text)
        if reply:
            print(reply)


if __name__ == "__main__":
    main()
