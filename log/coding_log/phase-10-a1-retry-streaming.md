## 重要：A1 — LLM 调用链路容错

### A1-1：Retry 工具函数

- 新增 `agent/retry.py` — `jittered_backoff()` 纯函数
  - 指数退避 + jitter，防止 thundering herd
  - 支持自定义 base_delay / max_delay / jitter_ratio
  - 11 条测试覆盖边界（0 base、封顶、jitter 范围、随机性、单调性）

### A1-2：LLM 调用容错封装

- 重构 `agent/loop.py`，将 `self.client.chat.completions.create(...)` 抽离为统一入口
- 新增 `_call_with_retry(fn)` 模板方法，内置异常分类：
  | 类型 | 行为 |
  |---|---|
  | `BadRequestError` (400) | 不重试，立即抛 RuntimeError |
  | `RateLimitError` (429) | jittered backoff 重试，最多 `max_retries` 次 |
  | `APIStatusError` (502/503/504) | 重试 |
  | `APITimeoutError` / `APIConnectionError` | 重试 |
  | 其他 | 重试后抛 |
- 默认 `max_retries=3`

### A1-3：Streaming 支持

- 新增 `_call_llm_streaming(kwargs)` 流式调用
  - 逐 chunk 输出 `delta.content` 到终端（实时可见）
  - tool_calls 分块累积（index→ id/name/arguments 拼接）
  - 纯文本回复结束后自动换行
- `AIAgent.__init__` 新增 `stream=True` / `max_retries=3` 参数
- CLI 新增 `--no-stream` 关闭（默认开启）

### A1-4：优雅迭代上限处理

- 工具死循环检测：`_detect_tool_loop()` 统计同一工具+同参数签名
  - 连续重复 > 3 次 → 注入错误消息让 LLM 换策略
  - 不同参数忽略，不同工具忽略
  - 每轮 `run_conversation` 重置计数器
- 达到 `max_iterations` 后返回更友好的提示（含"请简化请求"）

## 普通：重构说明

- `run_conversation` 的 LLM 调用从内联调用改为 `_call_llm` / `_call_llm_streaming` 统一入口
- 异常分类逻辑放在 `_call_with_retry` 中，`BadRequestError` 优先于 `APIStatusError` 捕获（子类在前）
- `_build_assistant_msg` 兼容 `SimpleNamespace`（流式返回）和 SDK 对象（非流式）

## 细微：修正

- `test_environment.py` 路径安全测试中的字符串匹配修复（Phase 9 遗留，已随 Phase 9 log 修正）
- `test_loop.py` 新增 mock_openai_raw fixture 用于异常模拟测试
- 全量 209 条测试通过（新增 24 条）
