# Phase 4 — Memory + Wiring

## Wiring 架构：组合根集中连线

各模块在编译期零依赖，运行时由 `agent/cli.py`（组合根）集中连接。

```
cli.py
  │
  ├─ import tool.builtins          # side-effect：触发工具的 registry.register() 自注册
  │
  ├─ AIAgent ──────────────────┬── registry    (属性注入)
  │                            ├── tool_names  (resolve_toolset & registry.tool_names 取交集)
  │                            └── memory      (MemoryStore 实例注入)
  │
  └─ tool.builtins.memory ──────── _store      (模块级变量注入)
```

### 三个关键连线方式

| 方式 | 示例 | 适用场景 |
|------|------|---------|
| **模块级单例直接 import** | `registry` 在 `tool/registry.py` 定义，cli 直接 import | 本身就是单例且无外部依赖 |
| **属性注入** | `agent.registry = registry` | agent 需要持有但与构造参数无关的依赖 |
| **模块变量注入** | `memory_tool._store = store` | 工具需要访问外部资源但 tool/ 必须零依赖 |

### tool.memory 和 memory_store 解耦
tool.memory（存储定义/接口） 无法直接import store（存储实现），因为是零依赖，但是使用_store 作为指针，在wiring阶段指向store，所以在dispatch时，调用tool.memory，exe 会使用store的方法

## python 模块即单例
一个单独的python文件即可视为一个单例
例如 a.py  的第一行是 a = 10, 那么在import a.py 时，a 已经存在了（导入即执行）
再次执行 import a.py as aa 时，aa 会指向已存在的实例，不会重新执行第一行的赋值语句，同时可以使用aa.a = 20 来修改 a 的值。
-(python 的模版需要使用class)

### 与 Hermes 的对比

Hermes 的做法是**「schema 走 registry，执行走拦截」**：

```
LLM 看到 → registry 里的 schema（memory_read / memory_write 定义）
LLM 调用 → agent._invoke_tool() 拦截到 "memory"
           → 直接调 memory_tool(store=self._memory_store)
           → 不走 registry.dispatch()
```

registry 在 Hermes 里正常注册了 memory tool 的 schema，**LLM 能感知到这个工具**，但调用时 dispatch 被 bypass 了。

chips-agent 当前选择所有工具走统一 dispatch：registry 既负责 schema 注册也负责执行路由，memory 的 `_store` 引用在 wiring 时注入。工具少（2 个）时统一 dispatch 更简单，等工具到两位数再评估是否切换到拦截模式。

| 维度 | chips-agent | Hermes |
|------|------------|--------|
| schema 注册 | registry | registry |
| 执行路由 | registry.dispatch | agent._invoke_tool 拦截 |
| memory 如何拿 store | 模块变量 `_store` 注入 | agent 传入（`store=self._memory_store`） |
| agent 复杂度 | 低，loop 通用 | 高，有特殊分支 |

## 读取记忆
1) llm 可以看到memory_read tool 的 schema，通过tool读取记忆
2) 初始化system prompt，包含记忆内容
```
def build(self, memory_snapshot: str = "") -> str:
        prompt = DEFAULT_SYSTEM_PROMPT
        if memory_snapshot:
            prompt += f"\n\n{memory_snapshot}"
        return prompt
```


## 零记忆时写入的上下文

> user发起message申请添加记忆 -> llm思考生成memory_write tool调用 -> dispatch 写入记忆(工具执行) -> 工具发起message通知记忆添加成功 -> llm返回回复
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
          "content": "以后叫我大王"
        }
      ],
      "max_tokens": 4096,
      "tools": [
        {
          "type": "function",
          "function": {
            "name": "memory_write",
            "description": "保存一条持久化记忆，重启后仍然保留",
            "parameters": {
              "type": "object",
              "properties": {
                "content": {
                  "type": "string",
                  "description": "要记忆的内容"
                },
                "category": {
                  "type": "string",
                  "enum": [
                    "memory",
                    "user"
                  ],
                  "description": "记忆分类，memory=项目记忆, user=用户信息"
                }
              },
              "required": [
                "content"
              ]
            }
          }
        },
        {
          "type": "function",
          "function": {
            "name": "memory_read",
            "description": "读取持久化记忆，包含项目记忆和用户信息",
            "parameters": {
              "type": "object",
              "properties": {
                "category": {
                  "type": "string",
                  "enum": [
                    "memory",
                    "user"
                  ],
                  "description": "记忆分类，默认 memory"
                }
              }
            }
          }
        },
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
      "content": "好的，大王！我已经记住您这个称呼了。",
      "reasoning_content": "用户希望我以后称呼他为\"大王\"，这是一个用户信息，我应该保存起来。",
      "tool_calls": [
        {
          "id": "call_00_Xl72aVnMjAblnCLe6WMM2411",
          "type": "function",
          "function": {
            "name": "memory_write",
            "arguments": "{\"content\": \"用户希望被称呼为\\\"大王\\\"\", \"category\": \"user\"}"
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
          "content": "以后叫我大王"
        },
        {
          "role": "assistant",
          "content": "好的，大王！我已经记住您这个称呼了。",
          "reasoning_content": "用户希望我以后称呼他为\"大王\"，这是一个用户信息，我应该保存起来。",
          "tool_calls": [
            {
              "id": "call_00_Xl72aVnMjAblnCLe6WMM2411",
              "type": "function",
              "function": {
                "name": "memory_write",
                "arguments": "{\"content\": \"用户希望被称呼为\\\"大王\\\"\", \"category\": \"user\"}"
              }
            }
          ]
        },
        {
          "role": "tool",
          "tool_call_id": "call_00_Xl72aVnMjAblnCLe6WMM2411",
          "content": "已保存到 user"
        }
      ],
      "max_tokens": 4096,
      "tools": [
        {
          "type": "function",
          "function": {
            "name": "memory_write",
            "description": "保存一条持久化记忆，重启后仍然保留",
            "parameters": {
              "type": "object",
              "properties": {
                "content": {
                  "type": "string",
                  "description": "要记忆的内容"
                },
                "category": {
                  "type": "string",
                  "enum": [
                    "memory",
                    "user"
                  ],
                  "description": "记忆分类，memory=项目记忆, user=用户信息"
                }
              },
              "required": [
                "content"
              ]
            }
          }
        },
        {
          "type": "function",
          "function": {
            "name": "memory_read",
            "description": "读取持久化记忆，包含项目记忆和用户信息",
            "parameters": {
              "type": "object",
              "properties": {
                "category": {
                  "type": "string",
                  "enum": [
                    "memory",
                    "user"
                  ],
                  "description": "记忆分类，默认 memory"
                }
              }
            }
          }
        },
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
      "content": "已记录！以后我就称呼您为 **大王** 啦 🫡。有什么需要尽管吩咐！",
      "reasoning_content": "保存成功。现在我可以称呼用户为\"大王\"了。",
      "tool_calls": []
    }
  }
]
```