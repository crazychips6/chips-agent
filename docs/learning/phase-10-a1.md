# Phase 10 学习笔记

## Streaming 模式下 tool_calls 的处理

OpenAI 流式 API 将 tool_calls 拆成多个 chunk 分开发送：

```
Chunk 1: {index: 0, id: "call_1",     function: {name: "echo", arguments: ""}}
Chunk 2: {index: 0, id: "",            function: {name: "",     arguments: '{"text":'}}
Chunk 3: {index: 0, id: "",            function: {name: "",     arguments: '"hello"}'}}
```

### 关键设计

1. **按 `index` 分组累积** — 用 `dict[int, dict]` 以 `tc.index` 为 key，因为一次可能返回多个并行 tool_calls（分别有 index=0, 1, 2...）
2. **`id` 和 `name` 只出现在第一个 chunk**，后续 chunk 这些字段为空字符串，跳过即可
3. **`arguments` 是字符串片段**，必须逐 chunk `+=` 拼接成完整 JSON
4. **最终转 SimpleNamespace** — 让下游 `_build_assistant_msg` 能统一处理流式和非流式的消息

### 处理流程

```
chunk 进入 → delta.tool_calls 不为空？
  → 按 index 分组 → 累积 id / name / arguments
  → 所有 chunk 处理完毕 → 按 index 排序 → 转 SimpleNamespace
  → 交给 _build_assistant_msg（与非流式同路径）
```

## Streaming vs 非流式

Streaming 只影响观察方式，不影响 tool dispatch 时机和总耗时：

- 流式：content 边收边打印，tool_calls 等 stream 结束才 dispatch
- 非流式：一次性打印完 content，再 dispatch tool_calls
- 两条路径在 dispatch 处代码完全相同

Tool call 总是在 content 全部输出后才执行。Streaming 本质是"打字效果"，对速度无提升。

## 工具死循环检测

在 dispatch 前拦截，`tool_name:归一化args` 重复 ≥ 4 次直接注入 fail_result 给 LLM：

- **args 归一化**：`json.loads → json.dumps(sort_keys=True)`，防止 key 顺序不同误判
- **不执行工具**：跳过 `registry.dispatch()`，用错误消息让 LLM 自己换方案
- **不打断循环**：给 LLM 自愈机会，而不是抛异常退出

## LLM 重试机制

在推理函数外套 `_call_with_retry(fn, desc)` 壳， `for循环`包裹fn(),使用`try-except`实现：

- `fn()` 成功 → 直接返回结果
- `fn()` 抛出可重试异常（429/502-504/超时/断连/未知错误）→ `jittered_backoff(attempt)` 计算延迟：`min(base * 2^(n-1), max) + random(0, ratio * delay)`，等待后重试
- `fn()` 抛出不可重试异常（400/401/非5xx）→ 直接 raise，不重试
- 全部重试耗尽 → raise RuntimeError
