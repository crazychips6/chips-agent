# 快应用（QuickApp）设计

## 概念

快应用是一个**标准化工具生产流水线**，让 Agent 用确定性的流程把用户的模糊需求变成可靠的、可复用的工具。不是给 Python 脚本包层皮，而是给能力积累建立工程化闭环。

关键区别——普通 Python 脚本和快应用的对比：

| | 普通 Python 脚本 | 快应用 |
|--|---------------|--------|
| 谁写的 | 用户自己 | Agent 生成 |
| 怎么部署 | 手工放位置、手工配环境 | 标准化目录、自动加载 |
| Agent 知道它吗 | ❌ 不知道 | ✅ 注册了、有 schema、自动发现 |
| Agent 怎么用 | 每次重新敲命令摸索参数 | 通过 schema 自动生成正确调用 |
| Agent 下次还记得吗 | ❌ 不记得，下次再编一次 | ✅ 存着，直接用 |
| 质量 | 用户的水平说了算 | 模板约束 + schema 验证，下限固定 |
| 能否独立运行 | ✅ | ✅ |

## 定位

快应用和内置工具是同一件事的两端：

| 内置工具 (builtins/) | 快应用 (quick_apps/) |
|---|---|
| 开发者手动写 | Agent 自动生成 |
| 随项目发布 | 随使用累积 |
| 改代码要 PR | 随时改、随时增 |
| 对所有人可用 | 对当前用户可用 |

最终都进 `registry`，对 Agent 来说没有区别——都是 `schema + handler`。

## 核心原则

- **标准化目录约定**：所有快应用存在 `.chips/quick_apps/` 下，启动时自动加载
- **确定性流程**：模板固定、schema 固定、目录固定，Agent 不即兴写脚本
- **一次创建，永久复用**：创建后自动注册到 ToolRegistry，Agent 下次直接用
- **双重入口**：`registry.register()` 供 Agent 调用 + `if __name__ == "__main__"` 供独立 CLI 使用
- **零摩擦部署**：不需装包、不需改配置、不需重启
- **`toolset` 硬编码**：所有快应用强制注册为 `toolset="quick_apps"`，LLM 没有选择权

## 抽象层：QuickAppManager

需要一个比 `PluginManager` 薄、比裸调 `registry.register()` 厚的抽象层。

### 为什么需要

核心原因不是注册（仍然是 `registry.register()`），而是**执行模型不同**：

| | 内置工具 / 插件 | 快应用 |
|---|---|---|
| 注册入口 | `registry.register()` | `QuickAppManager._register()` |
| handler 执行 | 主进程直接调 | **spawn 子进程执行，捕获 stdout** |
| 生命周期 | 随项目发布 | create / delete / list |
| 元信息 | 不需要 | 文件路径、创建时间、能力声明 |

`PluginContext.register_tool()` 的 handler 在主进程跑，快应用必须走子进程隔离，所以需要一个 Manager 在注册时自动包装 spawn 逻辑。

### Manager 职责

```
QuickAppManager
├── create(name, code) → QuickApp    # 写文件 + 注册（handler=spawn wrapper）
├── load_all() → list[QuickApp]      # 启动时扫描目录，import 并注册
├── run(name, args) → str            # spawn 子进程执行
├── delete(name) → None              # 删文件 + deregister
└── list() → list[QuickApp]          # 列出所有快应用
```

### 和 PluginManager 的对比

| | PluginManager | QuickAppManager |
|---|---|---|
| 扫描目录 | `plugins/` | `quick_apps/` |
| 加载方式 | importlib 动态加载 | 启动时扫描 + create 时注册 |
| 注册 | `ctx.register_tool()` → `registry.register()` | `_register()` → `registry.register()` |
| handler 执行 | 主进程 | **子进程 spawn** |
| 额外职责 | hook 调度、skill 注册 | 模板生成、语法校验、CLI 入口管理 |
| 对作者暴露的接口 | `register(ctx)` 回调 | 不对 LLM 暴露注册接口，Manager 全权管理 |

PluginManager 暴露 `register(ctx)` 给第三方开发者，QuickAppManager 的"开发者"是 LLM，约束比人强，所以不需要回调协议，Manager 自己搞定所有注册逻辑。

### dispatch 无感知

```python
# registry.dispatch() 视角：
#   内置工具 handler：直接执行逻辑
#   快应用 handler：spawn wrapper → 起子进程 → 收 stdout → 返回字符串
#   两者都是 callable，dispatch 不关心内部实现
```

不需要给 `registry` 加 `is_quick_app` 标记，不需要 if/else 判断入口类型。

## 混淆问题：Tool vs QuickApp

### 三种可能的混淆及解法

| 层面 | 混淆？ | 解法 |
|------|--------|------|
| **LLM 调用工具** | 不需要区分 | LLM 只按 schema 调，混不混没影响 |
| **操作快应用（list/delete）** | 不混 | 按 `toolset="quick_apps"` 筛选，元工具自己知道去哪里查 |
| **名字冲突** | 可能混 | `create_app` 查重：`if name in registry.tool_names: 拒绝` |
| **dispatch 执行** | 不需要区分 | 都是 callable，handler 内部决定执行路径 |

真正需要系统级区分的是 `toolset` 字段。快应用一律注册为 `toolset="quick_apps"`，模板里写死，LLM 无权修改。内置工具 `toolset` 可能是 `"core"` `"terminal"` `"file"` 等，插件/MCP 工具是 `"plugin"`——`toolset` 就是来源标记，不需要额外字段。

### 操作和使用通道分离

```
Agent 视角：
  {name: "wechat_crawler", schema: {...}}  → 可以调
  {name: "file_write",     schema: {...}}  → 可以调
  → 两者没区别

管理视角（list / delete）：
  → 只查 toolset="quick_apps"
  → 只看到快应用
```

操作和使用的通道天然分离，不需要额外设计。

## 查重闸（防造轮子）

三层防御：

1. **创建前语义匹配**：用户描述 ↔ 现有工具名/描述，命中则拦截提示直接用
2. **Agent Prompt 约束**：System Prompt 写明"创建前检查是否已有同功能工具"
3. **注册时名字冲突检测**：`name in registry.tool_names` → 拒绝

## 两分法：PURE vs IO

| 类型 | 特征 | 运行模式 | 沙盒 |
|------|------|---------|------|
| PURE | 纯计算，无硬件穿透 | 每次 spawn 子进程，完事销毁 | ✅ LocalEnvironment 标准沙盒 |
| IO | 需要硬件/持久设备 | 常驻服务进程 | ⚠️ 能力声明 + 设备透传策略 |

## 沙盒模型

三层隔离（按需递增）：

1. **进程隔离**：`LocalEnvironment` 子进程，崩溃不影响主 Agent
2. **能力声明**：快应用声明需要的能力（microphone/network），用户审批
3. **容器化**：Docker 沙盒，用于高安全场景

## 实现计划

### 阶段一：最小闭环（MVP）

目标：用户说"做个 X 工具" → Agent 生成 → 注册 → 下一轮就能用。重启不丢。

- 目录约定 `.chips/quick_apps/`
- 代码模板（固定 handler 签名、schema 格式、`registry.register()`）
- `create_app` 工具
- 启动加载（import `.chips/quick_apps/` 下所有 `.py`）
- `quick_apps` toolset 条目
- 基础校验（语法检查 + 试注册验证）

验证标准：
```
用户说："做一个微信公众号爬虫"
  → Agent 创建 → 注册成功
  → 下一句 "爬 XX 公众号" → Agent 直接调 wechat_crawler
  → 退出重启 → 仍然能调
```

### 阶段二：质量工程

目标：把成功率从"看 LLM 心情"拉到"流程保证"。

- 模板强化：硬约束 handler 签名结构，嵌入标准错误处理样板
- 后生成校验管线：语法检查 → 空跑测试 → 输出格式验证
- 子进程执行：`run_app` 走 `LocalEnvironment` 隔离
- 依赖提示：检测代码中的 import，提示用户安装

### 阶段三：双重入口

目标：快应用同时是 CLI 工具，可被脚本化、管道化、定时化。

- 模板加 CLI 块：`if __name__ == "__main__"` 解析 argv JSON，调同一个 handler
- 可选：注册到 PATH

验证标准：
```
$ python ~/.chips/quick_apps/wechat_crawler.py '{"account":"XX"}'
→ 输出和 Agent 调用时一致
```

### 阶段四：IO 类型扩展

按需决定是否进入。议题：语音监听、摄像头监控等需要设备访问的快应用。

- 能力声明协议
- 审批流程（复用 `safety/approval.py`）
- 常驻进程管理模式
- 进程间通信

### 阶段五：生态（可选）

- 分享/导出：快应用打包成单文件
- Web UI 管理：在 `chips-agent-web` 中浏览/运行

## 设计目标

- 确定性：Agent 生成行为可预期
- 可积累：一次创建，永久可用
- 可靠性：语法校验、沙盒执行、错误兜底
- 零摩擦：不需要用户会 Python、改配置、重启
