#

## python 的模块隔离 （与C++对比）
-   Python：模块边界在运行时仍然存在。from tool.registry import registry 拿到的实际上是
  sys.modules['tool.registry'].registry，每个模块是独立命名空间对象，解释器帮你管着。
  - C++：编译单元（.cpp）在编译链接后边界就消失了，变成一个平坦的二进制。运行时没有任何"这个变量属于哪个文件"的概念，所
  以你需要手动通过语言特性（static、私有构造函数）来模拟"只有一个"的约束。

## 对话循环和工具循环

 CLI 层（cli.py）                    run_conversation（loop.py）
  ─────────────────────────           ─────────────────────────
  while True:                        for _ in range(max_iterations):
      input("> ")    ──── 用户输入 ───→   请求 LLM
      run_conversation()                      │
          │                              LLM 返回 tool_calls？
          │                              ├─ 是 → dispatch → 继续循环
          │                              └─ 否 → return 纯文本
      print(回复)   ←── 返回结果 ───

  外层是 对话 session 循环（每次输入开始一次新对话），内层是 工具调用 ReAct 循环（单次对话内多轮工具调用直到 LLM
  给最终回复）

  每个对话都是开始一个新的session循环，上下文保持一致。在每次loop中，又有一个react工具循环
  ```
for _ in range(max_iterations):
  ```
  当前逻辑为只要llm的回复中包含tool_calls，就继续循环、否则返回最终回复。
  max_iterations 限制了最大工具调用次数，防止一次对话中无限循环。

## 工具调用的流程

    user 输入 "echo hello"
         │
         ▼
    请求 LLM（system + tools + user 消息）
         │
         ▼
    LLM 返回 tool_calls（调 echo, text="hello"）
         │
         ▼
    dispatch("echo", {"text": "hello"}) → "hello"
         │
         ▼
    把工具结果追加到 messages（tool 角色）
         │
         ▼
    （下一轮循环）再次请求 LLM
    这次上下文多了 assistant 消息 + tool 结果
         │
         ▼
    LLM 看到工具已返回结果，输出纯文本回复
    → 循环结束，返回内容

>  llm可以在prompt中看到可用的tools，并且根据用户问题和tool_schema生成tool_calls。(tool可以不被加载，那么llm就看不到)


```
[
  {
    "request": {
      "model": "deepseek-v4-flash",
      "messages": [
        {
          "role": "system",
          "content": "你是 chips，一个通用 AI agent。\n你由多个解耦模块组成，当前处于开发阶段。\n你的目标是帮助用户完成各种任务。\n\n## 行为准则\n- 使用中文回答，技术术语不强行翻译\n- 如果缺少完成任务所需的信息，主动询问用户\n- 如果遇到错误，说明原因并提供解决方案"
        },
        {
          "role": "user",
          "content": "你好"
        },
        {
          "role": "assistant",
          "content": "你好！我是 **chips**，一个通用 AI agent 🤖\n\n很高兴见到你！我可以帮你完成各种任务，比如回答问题、编写代码、分析数据、提供建议等等。有什么我可以帮你的吗？请随时告诉我你的需求！😊",
          "reasoning_content": "用户打了招呼，这是一个简单的问候。我可以直接回复问候，不需要调用工具。"
        },
        {
          "role": "user",
          "content": "echo hello"
        }
      ],
      "max_tokens": 4096,
      "tools": [
        {
          "type": "function",
          "function": {
            "name": "echo",
            "description": "原样返回输入文本",
            "parameters": {
              "type": "object",
              "properties": {
                "text": {
                  "type": "string",
                  "description": "要回显的文本"
                }
              },
              "required": [
                "text"
              ]
            }
          }
        }
      ]
    },
    "response": {
      "content": "",
      "reasoning_content": "用户让我执行 echo 工具，传入 \"hello\"。\n\n等等，当前 function 没有任何参数 schema？让我重新检查一下。\n\n工具 echo 的参数是 \"text\"，类型是 string，描述是\"要回显的文本\"。所以应该传入 {\"text\": \"hello\"}。",
      "tool_calls": [
        {
          "id": "call_00_GLsK7ep0GZU8WpiOxnhI8934",
          "type": "function",
          "function": {
            "name": "echo",
            "arguments": "{\"text\": \"hello\"}"
          }
        }
      ]
    }
  },
  {
    "request": {
      "model": "deepseek-v4-flash",
      "messages": [
        {
          "role": "system",
          "content": "你是 chips，一个通用 AI agent。\n你由多个解耦模块组成，当前处于开发阶段。\n你的目标是帮助用户完成各种任务。\n\n## 行为准则\n- 使用中文回答，技术术语不强行翻译\n- 如果缺少完成任务所需的信息，主动询问用户\n- 如果遇到错误，说明原因并提供解决方案"
        },
        {
          "role": "user",
          "content": "你好"
        },
        {
          "role": "assistant",
          "content": "你好！我是 **chips**，一个通用 AI agent 🤖\n\n很高兴见到你！我可以帮你完成各种任务，比如回答问题、编写代码、分析数据、提供建议等等。有什么我可以帮你的吗？请随时告诉我你的需求！😊",
          "reasoning_content": "用户打了招呼，这是一个简单的问候。我可以直接回复问候，不需要调用工具。"
        },
        {
          "role": "user",
          "content": "echo hello"
        },
        {
          "role": "assistant",
          "content": "",
          "reasoning_content": "用户让我执行 echo 工具，传入 \"hello\"。\n\n等等，当前 function 没有任何参数 schema？让我重新检查一下。\n\n工具 echo 的参数是 \"text\"，类型是 string，描述是\"要回显的文本\"。所以应该传入 {\"text\": \"hello\"}。",
          "tool_calls": [
            {
              "id": "call_00_GLsK7ep0GZU8WpiOxnhI8934",
              "type": "function",
              "function": {
                "name": "echo",
                "arguments": "{\"text\": \"hello\"}"
              }
            }
          ]
        },
        {
          "role": "tool",
          "tool_call_id": "call_00_GLsK7ep0GZU8WpiOxnhI8934",
          "content": "hello"
        }
      ],
      "max_tokens": 4096,
      "tools": [
        {
          "type": "function",
          "function": {
            "name": "echo",
            "description": "原样返回输入文本",
            "parameters": {
              "type": "object",
              "properties": {
                "text": {
                  "type": "string",
                  "description": "要回显的文本"
                }
              },
              "required": [
                "text"
              ]
            }
          }
        }
      ]
    },
    "response": {
      "content": "工具执行成功！`echo` 将你输入的文本原样返回了：**hello** ✅\n\n有什么其他需要帮忙的吗？😊",
      "reasoning_content": "工具成功执行，返回了 \"hello\"。这是 echo 工具的功能——原样返回输入文本。",
      "tool_calls": []
    }
  }
]
```

