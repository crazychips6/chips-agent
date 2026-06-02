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

### 注意

- 不能用 `json.dumps(args, sort_keys=True)` 做死循环检测的 key，因为 streaming 模式下 arguments 还没累积完。需要在工具派发阶段（tool_calls 已完整）再做 loop 检测。
- `SimpleNamespace` 替代 `MagicMock`，避免生产代码依赖 `unittest.mock`。
