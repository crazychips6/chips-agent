# Phase 2 — ToolRegistry + 工具派发

## 重要

- 创建 `tool/registry.py`：ToolRegistry 单例
  - `register(name, schema, handler, check_fn, ...)` 注册
  - `dispatch(name, args) → str` 派发（异步桥接 + 异常捕获）
  - `get_definitions(tool_names) → list[dict]` 获取 schema（check_fn TTL 30s）
  - 线程安全（RLock）
- 创建 `tool/builtins/echo.py`：自注册的 echo 工具，模块级调用 `registry.register()`
- 重写 `agent/loop.py`：ReAct 循环集成 tool_calls
  - API 请求附带工具定义
  - 收到 tool_calls → dispatch → 追加 tool 结果 → 继续循环
  - 处理 DeepSeek `reasoning_content` 思考模式
- 更新 `agent/cli.py`：启动时 `import tool.builtins` 触发自注册，将 registry 注入 agent

## 普通

- `tool/builtins/__init__.py`：显式 import echo 子模块以触发注册
- `tool/registry.py`：增加 `tool_names` 属性暴露注册的工具名列表

## 细微

- 修复 DeepSeek reasoning_content 回传问题
