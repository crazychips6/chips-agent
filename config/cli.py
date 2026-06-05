"""chips config 子命令处理"""

import sys

from config.store import ConfigStore


def handle_config(args):
    """根据 args.config_action 派发 list/get/set。"""
    store = ConfigStore()

    if args.config_action == "list":
        data = store.list_all()
        if not data:
            print("(空)")
            return
        for k, v in sorted(data.items()):
            print(f"  {k}: {v}")

    elif args.config_action == "get":
        val = store.get(args.key)
        if val is None:
            print(f"未设置: {args.key}")
            sys.exit(1)
        print(val)

    elif args.config_action == "set":
        store.set(args.key, args.value)
        print(f"已设置 {args.key} = {args.value}")
