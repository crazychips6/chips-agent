"""chips CLI 入口"""

import argparse
import os

from dotenv import load_dotenv

from agent.loop import AIAgent
from memory.store import MemoryStore
from tool.registry import registry
from tool.toolsets import resolve_toolset

# 模块级 side-effect import：触发 builtins 目录下各工具的 registry.register() 自注册
import tool.builtins  # noqa: F401


def main():
    load_dotenv()

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
    args = parser.parse_args()

    if args.version:
        print("chips 0.1.0")
        return

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("错误: 未设置 DEEPSEEK_API_KEY")
        print("请在 .env 文件中配置: DEEPSEEK_API_KEY=sk-...")
        return

    agent = AIAgent(api_key=api_key, base_url=args.base_url, model=args.model, debug_context=args.debug_context)
    # 临时手动 wiring，后续阶段会改为构造注入
    agent.registry = registry
    agent.tool_names = resolve_toolset(args.toolset) & registry.tool_names

    # 记忆系统 wiring（同步注入到 agent 和 memory 工具模块）
    import tool.builtins.memory as memory_tool

    memory_store = MemoryStore(memory_dir=".memory")
    agent.memory = memory_store
    memory_tool._store = memory_store

    if args.message:
        reply = agent.run_conversation(args.message)
        print(reply)
        return

    memory_snapshot = agent.memory.for_system_prompt() if agent.memory else ""
    memory_lines = len([l for l in memory_snapshot.split("\n") if l.strip()]) if memory_snapshot else 0
    print(f"chips v0.1.0 — model: {args.model}  base_url: {args.base_url}")
    print(f"工具集: {args.toolset}  |  已加载工具: {len(agent.tool_names)}  |  记忆: {memory_lines} 行")
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
        print(reply)


if __name__ == "__main__":
    main()
