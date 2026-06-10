# Tool Hot Zone — 智能工具集懒加载方案

> 状态: 设计阶段（待实现） | 优先级: 先扩工具集，再做实验

## 动机

当前 `tools` 参数每轮把所有 enabled toolset 的 schema 发给 LLM（~2000 tokens），
浪费大量 token 且 LLM 需要从过多选项中选择。随着工具增多，问题会更严重。

**目标**: 只让 LLM 看到「当前需要」的工具，需要时自己发现并激活。

---

## 架构

### 三层工具状态

```
tools 参数 = [core — 始终在] + [hot zone — TTL 3 轮] + [permanent — 启动时注入]
```

| 状态 | 行为 | 例子 |
|------|------|------|
| **core** | 始终暴露给 LLM，不可禁用 | `echo`, `memory`, `clarify`, `todo`, `toolset` |
| **hot zone** | 由 LLM 调用 `toolset("enable")` 激活，3 轮无使用自动退出 | `web`, `file`, `terminal` |
| **permanent** | 启动时 `--toolset` 指定，永久留在 tools 参数中 | `--toolset core,web` 则 web 常驻 |

### 核心流程

```
用户输入
  │
  ├─ 首轮匹配情景模式（可选）
  │   用户说"帮我搜索..." → 预激活 web
  │
  轮次开始
  1. resolve_tool_names() = core ∪ hot_zone ∪ permanent
  2. 发给 LLM（tools 参数 + system prompt 中的可用性表）
  3. LLM 判断：
       a. 现有工具够用 → 直接调用
       b. 不够用 → 调 toolset("list") 查看 → toolset("enable", "xxx")
  4. 工具调用 → 重置该 toolset 在 hot zone 的 TTL = 3
  5. 轮次结束 → hot zone 各 toolset TTL -= 1，== 0 的自动退出
```

### Hot TTL 规则

| 参数 | 默认值 | 说明 |
|------|--------|------|
| max_idle_rounds | 3 | 无调用的 toolset 自动退出的轮次阈值 |
| reset_on_use | true | 被调用过一次就满血重置 |

**重置粒度**: 整个 toolset。`web_search` 被调 → `web` 整个 toolset 满血 3 轮。

---

## 情景模式（备选，非必须）

启动时预激活一组 toolset 作为 hot zone 的初始值：

```
research  → hot zone 初始 = {web}
coding    → hot zone 初始 = {terminal, file}
debug     → hot zone 初始 = {terminal, file, web}
```

触发方式：
- **显式**: `--toolset core,coding` 或 `--toolset core --mode coding`
- **隐式**: 首条消息匹配关键词（实验性质，后续再实现）

---

## 实现计划

### Step 1: 扩工具集（先做）

将现有工具从单 `core` 拆分为分组的 toolset + 补全缺失工具：

| 工具集 | 工具 | check_fn |
|--------|------|----------|
| core | echo, memory, clarify, todo, toolset | 无 |
| terminal | terminal | 始终可用 |
| file | file_read, file_write, file_search | 始终可用 |
| web | web_search, web_fetch | `TAVILY_API_KEY` 或等效配置 |
| vision | screenshot | `DISPLAY` / `WAYLAND` 检测 |
| skills | skills_list, skill_view, skill_manage | skills 目录存在 |

`core` 改为 `includes: ["terminal", "file"]` 兼容 `--toolset core` 行为不变。

**新增工具**（补齐不足）:
- `file_search` — 文件内容搜索（ripgrep / grep）
- `web_fetch` — 可能已有
- `skill_manage` — 创建/编辑/删除技能
- `process` — 进程管理（top, ps, kill）
- `execute_code` — 代码执行（子进程跑脚本）

### Step 2: Hot Zone 机制

- `hot_zone: dict[str, int]` — toolset 名 → 剩余轮次
- `resolve_tool_names()` 改为合并 core + permanent + hot_zone
- `toolset("enable")` 将 toolset 加入 hot_zone，TTL = 3
- 每轮结束后 hot_zone 所有 entry TTL -= 1，== 0 的 pop
- 工具被调用时重置该工具所属 toolset 的 TTL

### Step 3: 情景模式（可选）

- `--mode` CLI 参数，映射为预置的 hot set
- 首条消息隐式匹配做一次预激活

---

## 与原 `toolset_tool.py` 的关系

不冲突。`toolset()` 工具继续存在，增加语义：

| 操作 | 行为 |
|------|------|
| `toolset("enable", "web")` | web 加入 hot zone，TTL=3 |
| `toolset("disable", "web")` | web 移出 hot zone |
| `toolset("list")` | 显示所有 toolset + hot zone 状态 + 剩余轮次 |
| `toolset("pin", "web")` | web 从 hot zone 提升到 permanent |

`disable` / `pin` 是新增语义，后续可选。

---

## Token 收益估算

| 场景 | 当前 | 改进后 |
|------|------|--------|
| core only | ~400 (echo,memory,clarify,todo,toolset) | ~400 |
| core + web | ~900 | ~900 |
| core + file + web + terminal | ~1500 | ~1500 |
| core + 全量 | ~3000 | ~400 (核心) + 按需激活 |

热点场景（如 coding 需要 terminal+file 常驻）实际收益 ~50-70%。
稀疏场景（用户只是聊天）收益最大，从不激活 hot zone。
