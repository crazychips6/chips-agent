"""chips CLI 入口

职责仅限于：
  1. 解析命令行参数
  2. 分发子命令（config / session / plugin / web）
  3. 调用 agent/boot.py 做实际组装启动

系统接线详情见 agent/boot.py —— 此处不做接线。
"""

import os
import sys

from dotenv import load_dotenv

from config.store import ConfigStore


def _build_parser():
    import argparse
    parser = argparse.ArgumentParser(
        prog="chips6",
        description="A general-purpose agent harness",
    )
    parser.add_argument("--model", default=os.getenv("CHIPS_MODEL", "deepseek-chat"))
    parser.add_argument("--base-url", default=os.getenv("CHIPS_BASE_URL", "https://api.deepseek.com"))
    parser.add_argument("--message", "-m", help="Single message and exit")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--debug-context", action="store_true",
                        help="将每轮 LLM 请求/响应写入 log/debug/session.json")
    parser.add_argument("--toolset", default="core", help="使用的工具集，默认 core")
    parser.add_argument("--no-memory", action="store_true", help="禁用记忆系统")
    parser.add_argument("--holographic", action="store_true",
                        help="启用 Holographic 记忆（SQLite 事实存储 + 语义检索）")
    parser.add_argument("--verbose", action="store_true", help="显示 system prompt 各层详情")
    parser.add_argument("--resume", nargs="?", const=True, default=False,
                        help="恢复上次会话，或指定 session_id 恢复特定会话")
    parser.add_argument("--no-stream", action="store_true", help="禁用 streaming 输出")
    parser.add_argument("--env", default="local", choices=["local", "docker"],
                        help="执行环境: local（本地）或 docker（容器沙盒）")
    parser.add_argument("--docker-image", default="alpine:latest",
                        help="Docker 环境使用的镜像名（仅在 --env=docker 时生效）")
    parser.add_argument("--no-compress", action="store_true", help="禁用上下文压缩")
    parser.add_argument("--auto-plan", action="store_true", default=False,
                        help="（已弃用）安全拦截始终启用，此参数不再生效")
    parser.add_argument("--no-auto-plan", action="store_true", default=False,
                        help="（已弃用）安全拦截始终启用，此参数不再生效")

    # 子命令
    subparsers = parser.add_subparsers(dest="command")

    config_cmd = subparsers.add_parser("config", help="管理配置")
    config_sub = config_cmd.add_subparsers(dest="config_action", required=True)
    config_sub.add_parser("list", help="列出所有配置")
    config_get = config_sub.add_parser("get", help="获取配置值")
    config_get.add_argument("key", help="配置键名")
    config_set = config_sub.add_parser("set", help="设置配置值")
    config_set.add_argument("key", help="配置键名")
    config_set.add_argument("value", help="配置值")

    plugin_cmd = subparsers.add_parser("plugin", help="管理插件")
    plugin_sub = plugin_cmd.add_subparsers(dest="plugin_action", required=True)
    plugin_sub.add_parser("list", help="列出已安装插件")
    plugin_install = plugin_sub.add_parser("install", help="安装插件")
    plugin_install.add_argument("path_or_package", help="插件文件路径")
    plugin_remove = plugin_sub.add_parser("remove", help="卸载插件")
    plugin_remove.add_argument("name", help="插件名")
    plugin_info = plugin_sub.add_parser("info", help="查看插件详情")
    plugin_info.add_argument("name", help="插件名")

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

    web_cmd = subparsers.add_parser("web", help="启动 Web 聊天界面")
    web_cmd.add_argument("--host", default="0.0.0.0", help="监听地址")
    web_cmd.add_argument("--port", type=int, default=8648, help="监听端口")

    # 子命令：chips insight
    insight_cmd = subparsers.add_parser("insight", help="跨会话聚合报表")
    insight_sub = insight_cmd.add_subparsers(dest="insight_action", required=True)
    insight_sub.add_parser("cost", help="按模型费用排名").add_argument("--days", type=int, default=7)
    insight_sub.add_parser("tools", help="工具使用统计").add_argument("--days", type=int, default=7)
    insight_sub.add_parser("trend", help="每日费用趋势").add_argument("--days", type=int, default=30)
    insight_portrait = insight_sub.add_parser("portrait", help="单会话完整画像")
    insight_portrait.add_argument("session_id", help="会话 ID")
    insight_sub.add_parser("weekly", help="一键周报")

    # 子命令：chips router
    router_cmd = subparsers.add_parser("router", help="管理路由规则")
    router_sub = router_cmd.add_subparsers(dest="router_action", required=True)
    router_sub.add_parser("list", help="列出所有路由规则")
    router_sub.add_parser("test", help="运行规则测试用例")
    router_sub.add_parser("summary", help="显示引擎统计")
    router_sub.add_parser("init", help="初始化用户路由配置")
    router_sub.add_parser("reload", help="重新加载配置后测试")

    return parser


def main():
    load_dotenv()
    ConfigStore().apply_to_env()

    parser = _build_parser()
    args = parser.parse_args()

    # ── 子命令处理（不进入 boot） ──
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
    if args.command == "insight":
        _handle_insight(args)
        return
    if args.command == "router":
        _handle_router(args)
        return

    if args.version:
        print("chips 0.3.0")
        return

    # 前置检查
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("错误: 未设置 DEEPSEEK_API_KEY")
        print("请在 .env 文件中配置: DEEPSEEK_API_KEY=sk-...")
        return

    # ── 组装 + 启动（所有接线逻辑在 agent/boot.py） ──
    from agent.boot import run as boot_run
    boot_run(args)


def _handle_insight(args) -> None:
    """chips insight 子命令：跨会话聚合报表。"""
    import os
    from session.db import SessionDB
    db_path = os.path.join(os.getcwd(), ".chips", "sessions.db")
    if not os.path.isfile(db_path):
        print(f"⚠ 数据库文件不存在: {db_path}")
        return
    db = SessionDB(db_path)
    from agent.insights import InsightsEngine
    engine = InsightsEngine(db)
    if args.insight_action == "cost":
        print(engine.cost_by_model(days=args.days).format())
    elif args.insight_action == "tools":
        print(engine.tool_usage(days=args.days).format())
    elif args.insight_action == "trend":
        print(engine.daily_cost_trend(days=args.days).format())
    elif args.insight_action == "portrait":
        print(engine.session_portrait(args.session_id).format())
    elif args.insight_action == "weekly":
        print(engine.weekly_report().format())


def _handle_router(args) -> None:
    """chips router 子命令：管理安全拦截规则。"""
    from safety.guard import GuardEngine
    engine = GuardEngine(include_defaults=True)
    if args.router_action == "list":
        print(f"安全规则 (共 {len(engine.rules)} 条):")
        for r in engine.rules:
            print(f"  [{r.priority:4d}] {r.name:30s} -> {r.then:20s} # {r.reason}")
    elif args.router_action == "summary":
        print(engine.summary())
    elif args.router_action == "init":
        from safety.loader import write_default_config
        print(write_default_config())
    elif args.router_action == "reload":
        engine.reload()
        print(f"已重新加载 {len(engine.rules)} 条规则")


if __name__ == "__main__":
    main()
