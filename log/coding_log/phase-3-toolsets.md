# Phase 3 — TOOLSETS 工具选择层

## 重要

- 创建 `tool/toolsets.py`：
  - `TOOLSETS` 静态字典定义工具集分组（`core`、`all`）
  - `resolve_toolset(*names)` 递归展开，返回扁平工具名集合
  - 支持工具集嵌套引用（如 `all → core`）
  - 循环引用自动检测（`resolving` 集合），防止无限递归
- 更新 `agent/cli.py`：
  - 新增 `--toolset` CLI 参数，默认 `core`
  - wiring 时 `agent.tool_names = resolve_toolset(args.toolset) & registry.tool_names` 过滤
  - 启动信息中显示当前工具集

## 普通

- 创建 `test/test_toolsets.py`：7 个测试覆盖
  - 基本展开（core/all）
  - 多参数、空参数
  - 未知名作为工具名处理
  - 循环引用保护
  - 嵌套工具集引用

## 细微

- `docs/PLAN.md` 进度标记阶段 3 为 ✅
