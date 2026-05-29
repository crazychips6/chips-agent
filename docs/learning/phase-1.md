# Phase 1 学习笔记

## Agent 对话循环

最小结构就是一个 for 循环：

```
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
```

```
system = build()                  ← 每次重建（为动态注入预留）
response = [system, user, assistant, user, assistant...user_new_message]
self.messages.append(assistant:response)                <- 就是上下文，在当前阶段每次对话都只进行追加，没有上下文管理
```

自测：
- 你好
 - 你好！我是 chips，很高兴见到你。有什么任务需要我帮你完成吗？
- 今天天气如何
 - 目前我还没有接入实时的天气查询功能。不过，如果你告诉我你所在的城市或地区名称，我可以尝试帮你描述一下该地区的一般气候特征，或者建议你使用其他天气查询服务（比如天气App或网站）。请问你具体在哪个城市呢？
- 我第一个问题是什么
 - 你的第一个问题是“你好”。
> 很显然在没有任何tool的情况下，agent 是无法进行任何额外能力的，只能通过llm_api获得思考结果。但是获取了上下文是保存的，因为都加入self.message

关键点：

- `self.messages` 只存 user/assistant 轮次，system prompt 作为 API 的单独参数传入，不混在 messages 列表里
- system prompt 每次都重建是正常的——未来需要注入时间戳、session ID、动态上下文等
- 不重建也能跑（静态字符串），但失去了动态组装的能力
