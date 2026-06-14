# 快应用（QuickApp）设计

## 概念与定位

快应用是一个**自动化工具生产流水线**。核心模式是：Agent 理解用户的模糊需求 → 优先寻找现有开源项目或 CLI 工具 → 包装成 QuickApp → 自我验证可用性 → 交付用户。

**用户不是调试者。** 不需要看报错、不需要改参数、不需要理解 Python。用户只做两件事：提需求、给反馈。

```
传统模式（程序员）：
  需求 → LLM 写代码 → 用户跑 → 报错 → 用户看日志 → 改 → 再跑 → 循环

快应用模式（外行人）：
  需求 → Agent 找现成项目 → 包装 → 自我验证 → 交付用户 → 等反馈微调
                                     ↑
                              Agent 处理调试循环，用户不感知
```

### 和普通 Python 脚本的区别

| | 普通 Python 脚本 | 快应用 |
|--|---------------|--------|
| 谁写的 | 用户自己 | Agent 生成 |
| 怎么部署 | 手工放位置、手工配环境 | 标准化目录、自动加载 |
| Agent 知道它吗 | ❌ 不知道 | ✅ 注册了、有 schema、自动发现 |
| Agent 怎么用 | 每次重新敲命令摸索参数 | 通过 schema 自动生成正确调用 |
| Agent 下次还记得吗 | ❌ 不记得，下次再编一次 | ✅ 存着，直接用 |
| 谁在调试 | **用户** | **Agent**（自我验证，用户不感知） |
| 能否独立运行 | ✅（CLI 入口） | ❌（依赖 chips 运行时） |

### 和内置工具的关系

| 内置工具 (builtins/) | 快应用 (quick_apps/) |
|---|---|
| 开发者手动写 | Agent 自动生成 |
| 随项目发布 | 随使用累积 |
| 改代码要 PR | 随时改、随时增 |
| 对所有人可用 | 对当前用户可用 |

**最终都进 `registry`，对 Agent 来说没有区别——都是 `schema + handler`。**

## 核心流程

```
① 用户提需求
   "帮我做个公众号爬虫"
      │
      ▼
② Agent 检索可用项目
      ├─ 查 registry：已有工具？→ 直接提示使用
      ├─ 查 known-good 库：pip 包可用？→ 记录备选
      └─ 搜索 GitHub / 查已知 CLI 工具：有现成项目？
          → git clone 或包装 CLI
      │
      ▼
③ 生成包装代码
      ├─ 读项目 README / CLI --help → 理解输入输出
      ├─ 生成 QuickApp 包装代码（调用项目 CLI/Python API）
      └─ 注册到 ToolRegistry
      │
      ▼
④ Agent 自我验证
      ├─ 带样例参数跑一次
      ├─ 能出结果 → 通过
      └─ 不能 → 换方案重试或报错 "当前无合适方案"
      │
      ▼
⑤ 交付用户
      ├─ "已做好，现在说'爬 XX 公众号'就能用"
      └─ 用户使用后发现不对 → 反馈 → Agent 调整
          （用户仍然不碰代码，只描述"少了标题字段"）
```

**第一次成功率**取决于开源生态的覆盖度，而不是 LLM 的编程水平。包装一个已有的 ffmpeg 比 LLM 手写视频处理代码可靠一万倍。

## 设计目标

- **一次性开发成功率**：Agent 找项目 + 包装 + 自我验证，用户不需要参与调试循环
- **现有项目优先**：Agent 首先搜索已知 CLI 工具、GitHub 项目、pip 包，不自己造轮子
- **零摩擦**：不需要用户会 Python、改配置、重启、看报错
- **可积累**：一次创建，永久可用，越用越多

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
  触发创建：用户需求 → Agent 检索项目 → 返回草案卡片 → 确认后后台生成

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

用户如果在对话中提出新需求，Agent **不应在对话里生成代码**，而是先检索项目再返回预览卡片：

```
用户："帮我做个视频转 GIF 的工具"
  → Agent：分析需求
  → Agent 内部检索：ffmpeg（known CLI tools）→ 可行
  → 返回草案卡片（名称: video_to_gif, 参数: input/output/fps）
  → Web Chat 渲染成 UI 卡片
  → 用户点击"确认创建"
  → 后台：读 ffmpeg 文档 → 生成包装代码 → 自我验证 → 注册
  → "已做好，用 ffmpeg 包装的，说'把视频转成 GIF'就能用"
```

这样用户看到的是选项和确认按钮，不是代码和报错。

## 核心原则

- **现有项目优先**：Agent 首先找已知 CLI 工具、GitHub 项目、pip 包。找不到合适方案才自己写代码
- **Agent 负责验证**：生成代码后 Agent 自己跑一次，不通过就换方案，用户不参与调试
- **标准化目录**：所有快应用存在 `.chips/quick_apps/` 下，启动时自动加载
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
| 元信息 | 不需要 | 文件路径、创建时间、依赖声明 |

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
| 额外职责 | hook 调度、skill 注册 | 模板生成、自我验证、已知项目检索 |
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
| LLM 生成的包装代码有问题 | 低 | 子进程隔离兜底 |
| LLM 被 prompt injection 诱导生成恶意代码 | 中 | 需要用户配合注入，概率低 |
| **第三方库包含恶意代码** | **中** | 这是 Python 供应链安全问题，非快应用独有 |
| **克隆的 GitHub 项目有恶意代码** | **中** | 和 pip 安装同等级风险 |

### 核心立场

**chips 不需要解决开源生态已经存在的安全问题。** 用户从 GitHub 克隆的项目、从 PyPI 安装的库、从 apt 安装的 CLI 工具——都有供应链风险。这是整个生态的问题，没有人能彻底解决。chips 要做的不是替代安全软件，而是在自己的能力范围内做三件事。

### 三层防御策略

```
 风险判断
    框定风险边界：
    - 子进程 vs 主进程（子进程拿不到主 Agent 的环境变量和 session DB）
    - 已知项目 vs 未知项目（known-good 之外标记为待确认）
    - 文件系统和网络能否触及
        │
        ▼
 信任名单（known-good）
    优先让 LLM 用经过验证的工具：
    - CLI 工具：ffmpeg, pandoc, yt-dlp, tesseract, ImageMagick
    - pip 包：requests, beautifulsoup4, Pillow, openpyxl 等
    不在列表里的项目 → 提示用户审批后才使用
        │
        ▼
 兜底提醒
    - 使用未知 GitHub 项目时弹确认
    - 可选开启 Docker 沙盒模式
    - 代码创建时展示给用户看一眼
```

### 三层隔离（按需递增）

1. **进程隔离**：`LocalEnvironment` 子进程，崩溃不影响主 Agent
2. **已知库优先**：模板约束 + known-good 列表，Agent 优先用经过验证的项目
3. **容器化**：Docker 沙盒模式，用于高安全场景

### PURE vs IO 类型

| 类型 | 特征 | 运行模式 | 沙盒 |
|------|------|---------|------|
| PURE | 纯计算，无硬件穿透 | 每次 spawn 子进程，完事销毁 | ✅ LocalEnvironment 标准沙盒 |
| IO | 需要硬件/持久设备 | 常驻服务进程 | ⚠️ 能力声明 + 设备透传策略 |

### 可信度评估

```
terminal 执行 pip install 的风险  ≥  快应用包装 GitHub 项目的风险
```

两者面临完全相同的供应链攻击面。快应用至少多了进程隔离和代码展示，攻击面实际是收窄的。

## 检索策略（防造轮子 + 找现有项目）

Agent 在创建快应用时按以下优先级检索：

```
① registry 已有工具
   用户描述命中已有工具 → 直接提示使用，不创建
   │
   ▼
② known-good CLI 工具（ffmpeg, pandoc, yt-dlp 等）
   包装为 subprocess.run() 调用
   │
   ▼
③ known-good pip 包（requests, Pillow 等）
   包装为 Python import + 函数调用
   │
   ▼
④ 搜索 GitHub 项目
   评估：是否有 README、star 数、最近更新、CLI 入口
   可行 → git clone → 读 README → 生成包装代码
   │
   ▼
⑤ 自己写 Python 代码（兜底）
   只有在无可用的现有项目时才走
```

每下一步都是上一步找不到合适方案时的降级。Agent 不会跳过步骤④直接写代码。

## 包装代码模板（展示）

包装一个现有项目的 QuickApp 大致形态：

```python
"""QuickApp: video_to_gif — 用 ffmpeg 将视频转为 GIF"""

import subprocess, os, shutil

HANDLER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "video_to_gif",
        "description": "将视频文件转换为 GIF",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "视频文件路径"},
                "fps": {"type": "integer", "description": "帧率，默认 10"},
            },
            "required": ["input"],
        },
    },
}

def handler(args: dict) -> str:
    input_path = args["input"]
    fps = args.get("fps", 10)
    output_path = os.path.splitext(input_path)[0] + ".gif"

    if not shutil.which("ffmpeg"):
        return "错误：未安装 ffmpeg，请执行 sudo apt install ffmpeg"

    result = subprocess.run(
        ["ffmpeg", "-i", input_path, "-vf", f"fps={fps}", output_path, "-y"],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        return f"转换失败：{result.stderr[:500]}"
    return f"已生成 {output_path}"

if __name__ == "__main__":
    # 子进程入口，供 QuickAppManager.run() 内部调用
    import json, sys
    print(handler(json.loads(sys.argv[1])))
```

## Agent 能力的体现

快应用模式下 Agent 少写了大量代码，但它的能力不体现在写代码上，而体现在三个决策环节。

### 写代码之前

```
用户说："帮我做个视频转 GIF 的工具"
  → Agent 需要理解：视频转 GIF 是 ffmpeg 做的事
  → 需要知道：ffmpeg 的命令行参数是 -i -vf fps=
  → 需要判断：ffmpeg 是否已安装，没装有什么替代方案
  → 需要推理：转 GIF 的核心参数是帧率和分辨率，用户可能想不到提
```

这是知识工作，不是代码工作。Agent 在把用户的模糊需求翻译成现有工具的精确输入——这是 LLM 作为 reasoning engine 的核心能力。

### 写代码之后

```
包装完 → 跑一次 → 出结果 → 交付用户
  → 用户说："GIF 太大了"
  → Agent 不需要写新代码，回去改 ffmpeg 参数：
    - 加 -vf 降低分辨率
    - 加 fps 降低帧率
    - 或用 palettegen 优化 GIF 质量
  → 再跑一次 → 验证 → 更新
```

Agent 不仅知道"怎么改"，还知道"为什么这样改"——知道 palettegen 可以优化 GIF，这是训练知识在工作，不是代码本身。

### 出错时换方案

```
包装 ffmpeg → 跑 → 报错 "ffmpeg not found"
  → Agent：没装 ffmpeg → 换 Pillow 方案
  → 用 Python Imaging Library 转 GIF
  → 再验证 → 通过 → 交付
```

一个方案不行，Agent 能自己换另一个，用户完全不知道。代码量很小，决策量很大。

### 总结

| 工作 | 写了多少代码 | 用了多少推理 |
|------|-----------|-----------|
| 理解用户需求 | 0 行 | 多 |
| 检索并选择方案 | 0 行 | 多 |
| 生成包装代码 | ~10 行 | 中 |
| 自我验证 | 0 行 | 中 |
| 按反馈修改 | ~5 行 | 多 |
| 出错换方案 | 0 行 | 多 |

Agent 的能力不体现在它写了多少代码，而体现在写代码之前和之后做了什么决策。让 LLM 少写代码不是弱化 Agent，而是把 Agent 放在它最擅长的位置——做决策，而不是做体力劳动。

## 幻觉风险

包装现有项目的模式有两面性：LLM 写得更少，但它必须"记住"现有项目的接口，而记忆不可靠。

### 一：CLI 参数幻觉

```
Agent 包装 ffmpeg，生成：
  subprocess.run([
    "ffmpeg", "-i", input_path,
    "-vf", "fps=10,scale=320:-1",
    "-gifflags", "+transdiff",   ← 这个参数不存在
    output_path
  ])
```

LLM 在训练数据里见过大量 ffmpeg 命令，但它分不清哪些参数是真的、哪些是它自己组合的。子进程报错后 Agent 要自我修复，用户感受到的是"点确认之后卡住了"。

**减轻方案**：known-good 模板预置正确的参数样例，LLM 填空时只能选参数值，不能造参数名。

### 二：虚假验证通过

```
Agent 跑样例验证：
  → subprocess.run([...]) 返回了 stdout
  → 有输出 → Agent 判断通过
  → 实际上 stdout 是 ffmpeg 的报错信息
```

验证卡在尴尬位置：太松（只看有输出）放过错误，太紧（解析输出内容）可能把正确结果当错误拒绝。

**减轻方案**：验证不仅要跑，还要检查 stdout 长度、关键词命中、returncode。不做语义验证，但做基本的格式检查。

### 三：GitHub 项目幻觉

```
Agent 检索 "wechat crawler python"
  → 训练数据里有一个同名项目 → 推荐给用户
  → 实际上项目已归档，API 已变，甚至已删库
  → git clone 下来跑不通
```

LLM 的记忆有截止日期，GitHub 仓库可能改名、API 升级、删库。Agent 用训练数据推荐而不是实时查询，就会发生。

**减轻方案**：实时 git ls-remote 验证仓库存在 + 读 README 确认 API。

### 四：过度承诺

```
用户："图片里提取表格"
  → Agent 知道有 tesseract → 包装 → 交付
  → 用户跑出来效果很差
  → Agent 改参数再试 → 还是不行
  → 最终用户对工具的信任受损
```

Agent 不知道 tesseract 在表格提取上效果不如 camelot-py。它只知道"有这个东西"，不知道"这东西在这个场景下好不好用"。

**减轻方案**：known-good 库加场景适用性标记（如 "OCR 准确率标注"、"表格提取推荐 camelot-py"）。

### 减轻策略优先级

| 幻觉类型 | 优先度 | 手段 |
|---------|--------|------|
| CLI 参数幻觉 | ★★★★★ | known-good 模板预置参数样例，禁止 LLM 造参数名 |
| 虚假验证通过 | ★★★☆☆ | 结构化检查 stdout + returncode |
| GitHub 项目幻觉 | ★★★☆☆ | 实时验证仓库存在 |
| 过度承诺 | ★★☆☆☆ | known-good 加场景标记，逐步积累 |

最值得投入的是 known-good 模板预置——把常见 CLI 工具的正确参数写死在模板里，LLM 无法自由发挥。这个做好的话，参数幻觉几乎归零。

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
  {name: "video_to_gif",    schema: {...}}  → 可以调
  {name: "file_write",      schema: {...}}  → 可以调
  → 两者没区别

管理视角（list / delete）：
  → 只查 toolset="quick_apps"
  → 只看到快应用
```

操作和使用的通道天然分离，不需要额外设计。

## 实现计划

### 阶段一：最小闭环（当前）

目标：Agent 能检索现有项目 → 包装 → 自我验证 → 注册 → 用户能用。走通整条链。

#### QuickAppManager

- `QuickAppManager` 核心类（create / load_all / run / delete / list）
- `.chips/quick_apps/` 目录约定
- 子进程包装逻辑：`subprocess.run` + 捕获 stdout，超时杀死
- 启动加载：`cli.py` 和 `web/server.py` **两端都要**扫描目录并注册

#### 检索 + 包装能力

- **known-good 索引**：预置已知 CLI 工具和 pip 包的描述 + 调用方式
- **代码生成 prompt**：包含检索优先级规则（已知工具 → known-good 包 → GitHub → 自己写）
- **包装模板**：固定 handler 签名 + `subprocess.run()` 调用模式 + `if __name__ == "__main__"` 子进程入口
- 模板中写死 `toolset="quick_apps"`，LLM 无权修改

#### 内置工具

所有工具内部持独立的 `gateway` 引用，不阻塞主 Agent Chat。

- `get_quick_app_draft`：Agent 分析需求 → 检索可用项目 → 返回草案卡片（包含选用的项目/方案）
- `create_app`：接收 draft → 按选定的方案生成包装代码 → `QuickAppManager.create()` → 注册
- `list_apps` / `delete_app`：委托 QuickAppManager

#### 自我验证

- `create_app` 生成后，Agent 用样例参数跑一次
- 通过 → 注册
- 不通过 → 换方案重试（最多 N 次）
- 全部不可行 → 返回 "当前无合适方案"

#### Toolset + Prompt

- `tool/toolsets.py` 加 `"quick_apps"` 条目
- `agent/prompt.py` 加约束：创建前先查 registry 和已知工具

#### 验证标准

```
用户说："做一个微信公众号爬虫"
  → Agent：检索可用项目 → 找到方案 → 返回草案卡片
  → 用户确认
  → 后端生成包装代码 → 自我验证 → 注册
  → "已做好，说'爬 XX 公众号'就能用"
  → 下一句 "爬 XX 公众号" → Agent 直接调 wechat_crawler
  → 退出重启 → 仍然能调
```

### 阶段二：Web 后端 API + 前端

目标：用户通过 Wizard 表单或 Chat 卡片创建和管理快应用。

#### 后端 API

`Draft` 和 `Create` 端点与内置工具共享底层逻辑（检索 + 模板 + Manager + gateway）：

| 端点 | 说明 |
|------|------|
| `POST /api/quick_apps/draft` | 用户 description → 检索 → draft card |
| `POST /api/quick_apps/create` | draft → 生成包装代码 → 验证 → 注册 |
| `GET /api/quick_apps` | 列表 |
| `DELETE /api/quick_apps/{name}` | 删除 + 注销 |

#### 前端 Wizard（创建入口）

Wizard 不是靠必填校验，而是 **Preview 让用户判断**：

```
Wizard 填写
  → Preview 展示草案卡片（名称 + 选用方案 + 参数 + 输出）
  → 用户看预览判断 "信息够不够？"
  → 确认 → 调 create_app → 自我验证 → 注册
```

| 字段 | 必填？ | 说明 |
|------|--------|------|
| 工具名称 | ✅ | registry 用 |
| 一句话描述 | ✅ | 检索和 schema 都用 |
| 输入参数（名称/类型/说明） | ✅ 至少一个 | 没参数 LLM 不知道怎么触发 |
| 选用方案 | ❌ | Agent 自动推荐，用户可改 |

#### 前端 Chat 卡片

- Agent 返回 draft 时，前端渲染确认卡片（含选用的项目信息）
- 用户点"确认" → 调 `create_app` → 完成后通知
- 前后端需约定 draft 传输协议

#### 前端 Dashboard

- 列表：名称、描述、选用方案、调用次数、状态
- 运行：内嵌输入表单，结果可视化
- 编辑：改参数/描述，重新生成
- 删除：一键移除

### 阶段三：检索能力增强

目标：扩大可检索的项目池，提高"一步到位"成功率。

- **GitHub 搜索集成**：用 GitHub API 搜索相关项目，评估可用性
- **本地 CLI 探测**：扫描当前机器的 `PATH`，自动发现已安装的 CLI 工具
- **pip 包索引**：维护可索引的已知包列表

### 阶段四：IO 类型扩展

按需决定。议题：语音监听、摄像头监控等需要硬件穿透的快应用。

- 能力声明协议
- 审批流程（复用 `safety/approval.py`）
- 常驻进程管理模式
- 进程间通信
