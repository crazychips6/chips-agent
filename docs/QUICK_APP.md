# 快应用（QuickApp）设计

## 概念与定位

快应用是一个**标准化工具生产流水线**，让 Agent 用确定性的流程把用户的模糊需求变成可靠的、可复用的工具。不是给 Python 脚本包层皮，而是给能力积累建立工程化闭环。

### 和普通 Python 脚本的区别

| | 普通 Python 脚本 | 快应用 |
|--|---------------|--------|
| 谁写的 | 用户自己 | Agent 生成 |
| 怎么部署 | 手工放位置、手工配环境 | 标准化目录、自动加载 |
| Agent 知道它吗 | ❌ 不知道 | ✅ 注册了、有 schema、自动发现 |
| Agent 怎么用 | 每次重新敲命令摸索参数 | 通过 schema 自动生成正确调用 |
| Agent 下次还记得吗 | ❌ 不记得，下次再编一次 | ✅ 存着，直接用 |
| 质量 | 用户的水平说了算 | 模板约束 + schema 验证，下限固定 |
| 能否独立运行 | ✅（CLI 入口） | ✅ |

### 和内置工具的关系

| 内置工具 (builtins/) | 快应用 (quick_apps/) |
|---|---|
| 开发者手动写 | Agent 自动生成 |
| 随项目发布 | 随使用累积 |
| 改代码要 PR | 随时改、随时增 |
| 对所有人可用 | 对当前用户可用 |

**最终都进 `registry`，对 Agent 来说没有区别——都是 `schema + handler`。**

## 设计目标

- **确定性**：Agent 生成行为可预期（模板固定、schema 固定、目录固定）
- **可积累**：一次创建，永久可用，越用越多
- **可靠性**：语法校验、沙盒执行、错误兜底
- **零摩擦**：不需要用户会 Python、改配置、重启

## 交互模型

### 为什么不在 CLI 做创建

CLI 纯文本交互下，用户创建快应用只能靠一句自然语言描述。这对 LLM 来说信息密度太低——用户说"爬公众号"，LLM 得猜：爬什么平台？要什么字段？是否要登录？输出格式？而 Web UI 的 Wizard 表单可以把这些拆成结构化输入，用户看着选项填，比让 LLM 猜准确得多。

### 三端职责

```
Web UI（管理）
  创建：Wizard 表单 → 参数/输出/依赖逐项确认 → 预览 → 确认生成
  管理：Dashboard（列表 + 调用次数 + 最后使用 + 状态）
  编辑：改参数/描述，重新生成
  删除：一键移除
  运行：内嵌输入表单，填完直接触发
  结果可视化：表格/图片/图表内嵌渲染

Web Chat（对话）
  调已有快应用：用户自然语言 → Agent 调 wechat_crawler → 返回结果
  触发创建：用户需求 → Agent 返回预览卡片 + "是否创建？" → 确认后跳转 UI

CLI（不支持）
  不感知快应用的存在。不暴露创建/编辑/删除/运行入口。
```

### Web Chat 调用流程

创建后的快应用注册到 ToolRegistry，Web Chat 的 Agent 和其他内置工具一样按 schema 调用。**Agent 不感知工具是内置还是快应用**，调用、回填、呈现的流程完全一致。

```
用户："爬 XX 公众号"
  → Agent：调 wechat_crawler {"account":"XX", "count":3}
  → QuickAppManager spawn 子进程 → Python 执行
  → 返回结构化结果
  → Agent：呈现给用户
```

### Web Chat 触发创建流程

用户如果在对话中提出新需求，Agent **不应在对话里生成代码**，而是返回预览卡片让用户确认：

```
用户："帮我做个 PDF 转图片的工具"
  → Agent：理解需求 → 调用 get_quick_app_draft 工具
  → 返回草案卡片（名称、参数、输出、依赖）
  → Web Chat 渲染成 UI 卡片
  → 用户点击"确认创建" → 跳转 UI 的 Wizard 页面（预填好参数）
  → 或直接在卡片上 "一键创建"
```

这样用户看到的是选项和确认按钮，不是代码和报错。

## 核心原则

- **标准化目录**：所有快应用存在 `.chips/quick_apps/` 下，启动时自动加载
- **确定性流程**：模板固定、schema 固定、目录固定，Agent 不即兴写脚本
- **一次创建，永久复用**：创建后自动注册到 ToolRegistry，Agent 下次直接用，重启不丢失
- **Web-only 创建和管理**：创建/编辑/删除只在 Web UI 中发生，CLI 不做
- **Web Chat 可调用**：创建后 Agent 按 schema 调用，不感知来源
- **`toolset` 硬编码**：所有快应用强制注册为 `toolset="quick_apps"`，LLM 没有选择权，这就是分界线

## QuickAppManager

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

## 安全模型

### 威胁边界

| 威胁来源 | 风险等级 | 说明 |
|---------|---------|------|
| LLM 生成的代码有意外副作用 | 低 | 子进程隔离兜底，用户创建时可见 |
| LLM 被 prompt injection 诱导生成恶意代码 | 中 | 需要用户配合注入，概率低 |
| **第三方库包含恶意代码** | **中** | 这是 Python 供应链安全问题，非快应用独有 |

### 核心立场

**chips 不需要解决 Python 生态已经存在的供应链安全问题。** 用户从 PyPI 下载的库可能有恶意代码——这是整个语言生态的问题，没有人能彻底解决。chips 要做的不是替代操作系统和安全软件，而是在自己的能力范围内做三件事。

### 三层防御策略

```
 风险判断
    框定风险边界：
    - 子进程 vs 主进程（子进程拿不到主 Agent 的环境变量和 session DB）
    - 已知库 vs 未知库（known-good 之外标记为待确认）
    - 文件系统和网络能否触及
        │
        ▼
 信任名单 （known-good）
    优先让 LLM 用经过验证的库：
    requests, beautifulsoup4, lxml, PyPDF2, python-docx,
    openpyxl, markdown, jinja2, Pillow, httpx, feedparser,
    yaml, toml
    不在列表里的库 → 提示用户审批后才安装
        │
        ▼
 兜底提醒
    - 安装未知库时弹确认
    - 可选开启 Docker 沙盒模式
    - 代码创建时展示给用户看一眼
```

### 三层隔离（按需递增）

1. **进程隔离**：`LocalEnvironment` 子进程，崩溃不影响主 Agent。恶意库在子进程里跑，拿不到主 Agent 的环境变量、session DB、主进程内存
2. **已知库优先**：模板约束 + known-good 列表，LLM 倾向于用经过验证的库
3. **容器化**：Docker 沙盒模式，文件系统和网络完全隔离。默认不用，仅在用户开启或安装未知库时触发

### PURE vs IO 类型

| 类型 | 特征 | 运行模式 | 沙盒 |
|------|------|---------|------|
| PURE | 纯计算，无硬件穿透 | 每次 spawn 子进程，完事销毁 | ✅ LocalEnvironment 标准沙盒 |
| IO | 需要硬件/持久设备 | 常驻服务进程 | ⚠️ 能力声明 + 设备透传策略 |

### 可信度评估

```
终端执行 pip install 的风险  ≥  快应用安装第三方库的风险
```

两者面临完全相同的供应链攻击面（PyPI 投毒、typosquatting、依赖混淆）。快应用至少多了进程隔离和代码展示，攻击面实际是收窄的。如果用户能接受在 terminal 里手动 `pip install`，快应用没有理由不能接受。

## 查重闸（防造轮子）

三层防御：

1. **创建前语义匹配**：用户描述 ↔ 现有工具名/描述，命中则拦截提示直接用
2. **Agent Prompt 约束**：System Prompt 写明"创建前检查是否已有同功能工具"
3. **注册时名字冲突检测**：`name in registry.tool_names` → 拒绝

## 混淆问题：Tool vs QuickApp

| 层面 | 混淆？ | 解法 |
|------|--------|------|
| **LLM 调用工具** | 不需要区分 | LLM 只按 schema 调，混不混没影响 |
| **操作快应用（list/delete）** | 不混 | 按 `toolset="quick_apps"` 筛选 |
| **名字冲突** | 可能混 | `create_app` 查重 |
| **dispatch 执行** | 不需要区分 | 都是 callable，handler 内部决定执行路径 |

真正需要系统级区分的是 `toolset` 字段。快应用一律注册为 `toolset="quick_apps"`，模板里写死，LLM 无权修改。`toolset` 就是来源标记，不需要额外字段。

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

## 实现计划

### 阶段一：最小闭环（当前）

目标：`create_app` 走通 → 注册 → Agent 能调 → 重启不丢。代码生成走独立 LLM call，不阻塞 Chat。

#### QuickAppManager

- `QuickAppManager` 核心类（create / load_all / run / delete / list）
- `.chips/quick_apps/` 目录约定
- 子进程包装逻辑：`subprocess.run` + 捕获 stdout，超时杀死
- 启动加载：`cli.py` 和 `web/server.py` **两端都要**扫描目录并注册，确保 Web Chat 的 Agent 也能发现快应用

#### 代码生成模板

- handler 固定签名 `def handler(args: dict) -> str`
- `HANDLER_SCHEMA` 结构固定（OpenAI function-calling 格式）
- `if __name__ == "__main__"` CLI 入口
- 模板中写死 `toolset="quick_apps"`，LLM 无权修改

#### 内置工具

所有工具内部持独立的 `gateway` 引用，不阻塞主 Agent Chat。

- `get_quick_app_draft`：轻量 LLM call，从用户描述提取 name / params / output / deps，返回草案卡片
- `create_app`：内部含**查重闸**（语义匹配 → 名字冲突检测），通过后调 LLM 填充模板生成代码 → `QuickAppManager.create()` → 注册
- `list_apps` / `delete_app`：委托 QuickAppManager

#### Toolset + Prompt

- `tool/toolsets.py` 加 `"quick_apps"` 条目
- `agent/prompt.py` 加一条约束：创建前必须检查是否已有同功能工具

#### 验证标准

```
用户说："做一个微信公众号爬虫"
  → Agent 返回预览卡片 → 用户确认
  → 后端生成 → 注册成功
  → 下一句 "爬 XX 公众号" → Agent 直接调 wechat_crawler
  → 退出重启 → 仍然能调
```

### 阶段二：Web 后端 API + 前端

目标：用户可以通过 Wizard 表单或 Chat 卡片创建和管理快应用。

#### 后端 API

`Draft` 和 `Create` 端点和内置工具共享底层逻辑（模板 + Manager + gateway），不重复：

| 端点 | 说明 |
|------|------|
| `POST /api/quick_apps/draft` | 用户 description → draft card |
| `POST /api/quick_apps/create` | draft → LLM 生成代码 → 注册 |
| `GET /api/quick_apps` | 列表 |
| `DELETE /api/quick_apps/{name}` | 删除 + 注销 |

#### 前端 Wizard（创建入口）

Wizard 表单不是靠必填校验来保证信息完整，而是用 **Preview 让用户判断**：

```
Wizard 填写
  → Preview 展示完整草案卡片（name / params / output / deps）
  → 用户看预览判断 "信息够不够？"
  → 确认 → 调 create_app 生成代码
  → 展示代码摘要（可选）→ 最终确认 → 注册
```

| 字段 | 必填？ | 说明 |
|------|--------|------|
| 工具名称 | ✅ | registry 用 |
| 一句话描述 | ✅ | LLM 用这个匹配调用场景 |
| 输入参数（名称/类型/说明） | ✅ 至少一个 | 没参数的工具 LLM 不知道怎么触发 |
| 输出描述 | ❌ | 可以从代码倒推 |
| 依赖库 | ❌ | import 自动检测 |

#### 前端 Chat 卡片

- Agent 返回 `get_quick_app_draft` 结果时，前端识别特殊格式并渲染确认卡片
- 用户点"确认" → 调 `create_app` → 完成注册
- 前后端需要约定 draft 数据的传输协议（WebSocket 消息格式）

#### 前端 Dashboard

- 列表：名称、描述、调用次数、最后使用、状态
- 运行：内嵌输入表单，结果可视化（表格/图片/图表）
- 编辑：改参数/描述，重新生成
- 删除：一键移除

### 阶段三：质量工程

目标：把成功率从"看 LLM 心情"拉到"流程保证"。

- 模板强化：嵌入标准错误处理样板
- 后生成校验管线：语法检查 → 空跑测试 → 输出格式验证
- **known-good 检测**：扫描代码中的 import，匹配 known-good 列表，不在列表内的弹用户审批
- 子进程执行：`run` 走 `LocalEnvironment` 隔离

### 阶段四：IO 类型扩展

按需决定。议题：语音监听、摄像头监控等需要硬件穿透的快应用。

- 能力声明协议
- 审批流程（复用 `safety/approval.py`）
- 常驻进程管理模式
- 进程间通信
