# 默认上下文压缩

## tool_result 一行摘要
- 裁剪前: [头部] [旧 tool: 5000 字符内容] [旧 tool: 3000 字符内容] [尾部]
                       ↓  替换为一行摘要
  裁剪后: [头部] [旧 tool: [read] main.py (5000 chars)] [旧 tool: [terminal] npm test (12 lines)] [尾部]
                                          ↓
                                LLM 摘要模型看到这些一行描述，
                                结合上下文理解发生了什么

  最终这些旧轮次会被 LLM 摘要替换，所以不需要保留完整内容——只要给摘要模型足够的信息知道"这里执行了什么工具"就行。

##  中间压缩
- 主要的压缩就先给tool_result压缩为一行，再根据头尾保留消息数量和预算减少token数计算出中间部分，中间部分直接交给l
  lm进行摘要化，至于压缩的怎么样主要看llm的效果。最后将压缩后的middle拼接回去，主要防止name重复和tool配对缺失就可以了
  。这就是压缩的全部过程吧

● 对，完全正确。就是这四步：

  1. 裁剪 — 旧的 tool 结果 → 一行描述（免费）
  2. 切分 — 保护头 N 条 + 尾 ~20K tokens，中间是要压缩的区域
  3. 摘要 — 中间区域交给 LLM，返回结构化摘要
  4. 拼接 — 头部 + 摘要 + 尾部，修复 tool_call/tool_result 配对

  效果确实取决于 LLM 的摘要质量——prompt 写得好不好直接影响"压缩后还能记得多少有用信息"。

## 工具对确实 （assistant-tool_calls, tool_results）
- "No tool call found for function call output with call_id call_a"

  因为 llm API 要求：
  - 每个 tool_call_id 必须有对应的 assistant.tool_calls 存在
  - 每个 assistant.tool_calls 后面必须跟一个同 id 的 tool 结果

  _sanitize_tool_pairs 就是干这件事的：

  - 孤儿 tool 结果：tool_call_id 对应的 assistant 已被删除 → 删掉这个 tool 消息
  - 缺失 tool 结果：assistant 发了 tool_calls 但对应的 tool 结果被删了 → 插入一条 stub "[Result from earlier
  conversation — see context summary]"
`