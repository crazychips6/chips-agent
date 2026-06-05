"""chips session 子命令处理"""

import sys
import time

from session.db import SessionDB


def handle_session(args):
    """根据 args.session_action 派发 list/show/search/delete。"""
    db = SessionDB(db_path=".chips/sessions.db")

    if args.session_action == "list":
        sessions = db.list_sessions()
        if not sessions:
            print("(无会话)")
            return
        for s in sessions:
            created = time.strftime("%m-%d %H:%M", time.localtime(s["created_at"]))
            title = s["title"] or "(无标题)"
            print(f"  {s['id']}  {created}  [{s['msg_count']}条]  {title}")

    elif args.session_action == "show":
        sess = db.get_session(args.session_id)
        if not sess:
            print(f"会话不存在: {args.session_id}")
            sys.exit(1)
        created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sess["created_at"]))
        updated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sess["updated_at"]))
        print(f"会话: {sess['id']}")
        print(f"创建: {created}")
        print(f"更新: {updated}")
        if sess["title"]:
            print(f"标题: {sess['title']}")

        # 显示消息摘要
        history = db.get_history(args.session_id)
        if history:
            print(f"\n消息 ({len(history)} 条):")
            for i, msg in enumerate(history, 1):
                role = {"user": "用户", "assistant": "助手", "tool": "工具"}.get(msg["role"], msg["role"])
                content = msg.get("content", "")
                preview = content[:60].replace("\n", " ") if content else "(空)"
                print(f"  {i}. [{role}] {preview}")

    elif args.session_action == "search":
        results = db.search(args.query)
        if not results:
            print(f"未找到匹配 '{args.query}' 的消息")
            return
        for r in results:
            ts = time.strftime("%m-%d %H:%M", time.localtime(r["created_at"]))
            role = {"user": "用户", "assistant": "助手", "tool": "工具"}.get(r["role"], r["role"])
            preview = r["content"][:100].replace("\n", " ") if r["content"] else "(空)"
            print(f"  [{ts}] {r['session_id'][:8]}…  [{role}] {preview}")

    elif args.session_action == "delete":
        sess = db.get_session(args.session_id)
        if not sess:
            print(f"会话不存在: {args.session_id}")
            sys.exit(1)
        db.delete_session(args.session_id)
        print(f"已删除会话: {args.session_id}")
