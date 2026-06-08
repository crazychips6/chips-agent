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

    elif args.session_action == "stats":
        sess = db.get_session(args.session_id)
        if not sess:
            print(f"会话不存在: {args.session_id}")
            sys.exit(1)
        stats = db.get_session_stats(args.session_id)
        created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sess["created_at"]))
        print(f"会话: {sess['id']}")
        print(f"创建: {created}")
        print(f"LLM 调用: {stats['call_count']} 次")
        print(f"Tokens:   {stats['total_prompt']:,} 输入 + {stats['total_completion']:,} 输出 = {stats['total_prompt'] + stats['total_completion']:,}")
        print(f"费用:     ${stats['total_cost']:.6f}")

        # 按模型统计
        rows = db.get_session_usage(args.session_id)
        if rows:
            by_model: dict[str, dict] = {}
            for r in rows:
                m = r["model"]
                if m not in by_model:
                    by_model[m] = {"calls": 0, "prompt": 0, "completion": 0, "latency": []}
                by_model[m]["calls"] += 1
                by_model[m]["prompt"] += r["prompt_tokens"]
                by_model[m]["completion"] += r["completion_tokens"]
                by_model[m]["latency"].append(r["latency_ms"])
            print("\n按模型:")
            for m, d in by_model.items():
                avg_lat = sum(d["latency"]) // len(d["latency"])
                print(f"  {m}: {d['calls']} 次, {d['prompt'] + d['completion']:,} tokens, {avg_lat}ms 平均延迟")

    elif args.session_action == "delete":
        sess = db.get_session(args.session_id)
        if not sess:
            print(f"会话不存在: {args.session_id}")
            sys.exit(1)
        db.delete_session(args.session_id)
        print(f"已删除会话: {args.session_id}")
