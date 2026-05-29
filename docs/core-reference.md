# Hermes Agent — Core Architecture Reference

> 剥离 gateway、cron、MCP、plugins、web UI、TUI、tests 后，Hermes 的核心骨架。
> 供自建 agent 时的架构参考。

## 代码规模 (去外围后)

| 层 | 路径 | 行数 | 说明 |
|---|------|------|------|
| Agent 主循环 | `run_agent.py` | ~13,800 | AIAgent class, ReAct loop, 全部工具派发 |
| 工具注册 | `tools/registry.py` | ~540 | ToolRegistry 单例 + 自注册机制 |
| 工具选择 | `toolsets.py` | ~800 | TOOLSETS 静态定义 + 递归组合 |
| 提示词组装 | `agent/prompt_builder.py` | ~1,120 | System prompt 7 层组装 |
| Session 存储 | `hermes_state.py` | ~2,100 | SQLite SessionDB, FTS5 全文搜索 |
| 日志系统 | `hermes_logging.py` | ~390 | 旋转日志 + Session 标记 + 脱敏 |
| 常量/路径 | `hermes_constants.py` | ~295 | HERMES_HOME, 路径, 环境检测 |
| 记忆工具 | `tools/memory_tool.py` | ~690 | 冻结快照模式, MEMORY.md + USER.md |
| 安全审批 | `tools/approval.py` | ~1,240 | 危险命令检测 + 交互审批 |
| 环境沙盒 | `environments/base.py` | ~790 | 抽象基类 + spawn-per-call 模型 |
| 本地沙盒 | `environments/local.py` | ~430 | 子进程隔离 + 凭证剥离 |
| Docker 沙盒 | `environments/docker.py` | ~650 | 容器隔离, --cap-drop ALL |
| 凭据透传 | `tools/env_passthrough.py` | ~150 | 技能声明 + 用户配置的 allowlist |
| CLI 入口 | `cli.py` | ~11,640 | 参数解析, 配置加载, 启动 agent |
| **核心总计** | | **~34,000** | 不含大面积工具单体 |

> 每个工具实现文件 (tools/*.py) 自身行数多在 300-3000 行之间，
> 但核心架构不依赖具体工具实现——工具通过 registry.register() 挂钩子。

---

## 一、总架构

三个模块组成最小骨架：Registry → Memory → Agent。

```
工具文件 (tools/*.py)           toolsets.py (TOOLSETS 定义)
    │ self-register                   │ resolve_toolset()
    ▼                                 ▼
ToolRegistry (单例) ----------- 工具名列表 --------→
    │                                                    │
    │ get_definitions()          dispatch()              │
    ▼                                                    ▼
AIAgent.run_conversation() ←── LLM ←── system prompt
    │                                                    │
    ├─ _build_system_prompt()                            │
    │    ├─ SOUL.md / 默认身份                            │
    │    ├─ Memory 快照 (frozen snapshot)                  │
    │    ├─ Skills 索引                                  │
    │    ├─ Context 文件 (AGENTS.md / CLAUDE.md)          │
    │    └─ 时间戳 + 平台提示                              │
    │                                                    │
    └─ ReAct 循环                                         │
         messages → API → tool_calls → dispatch → 结果     │
         → messages → API → ... → 最终回复                  │
```

**数据流**：
1. CLI 启动 → 加载配置 → 创建 SessionDB → 创建 AIAgent
2. AIAgent 构建 system prompt（含记忆快照）
3. `run_conversation(message)` 进入 ReAct 循环
4. 模型返回 text 或 tool_calls → dispatch → 结果追加到 messages
5. 无 tool_calls → 返回最终回复 → 日志写入 SessionDB

---

## 二、ToolRegistry —— 工具注册与派发

**路径**: `tools/registry.py`

核心模式：每个工具文件在模块加载时调用 `registry.register()` 自注册，
Agent 只依赖 registry 做 dispatch，不依赖具体工具实现。

### ToolEntry 结构

每个工具注册时携带：
- `name` — 工具名（全局唯一）
- `toolset` — 所属工具集（用于分组 + check_fn 继承）
- `schema` — OpenAI 格式的 function schema
- `handler` — 实际执行函数
- `check_fn` — 可用性检查（None = 始终可用）
- `is_async` — 是否异步
- `max_result_size_chars` — 结果截断阈值

### 注册流程

```python
# tools/terminal_tool.py (简化)
from tools.registry import registry

registry.register(
    name="terminal",
    toolset="terminal",
    schema={"name": "terminal", "description": "...", "parameters": {...}},
    handler=handle_terminal,
    check_fn=check_terminal_requirements,
)
```

Agent 什么都不用做——只要 tool 模块被 import，工具就自动注册了。

### 关键方法

```python
# 获取可用工具定义（过滤 check_fn）
def get_definitions(tool_names: Set[str]) -> List[dict]:
    # 只返回 check_fn() == True 的工具
    # check_fn 结果 TTL 缓存 30 秒

# 派发工具调用
def dispatch(name: str, args: dict) -> str:
    # 异步 handler 自动桥接
    # 所有异常捕捉并返回 {"error": "..."}
```

### check_fn TTL 缓存

`check_fn` 探针外部状态（Docker 是否运行、Playwright 是否安装），
结果缓存 30 秒。避免每次 `get_definitions()` 都重复探测。

```python
_CHECK_FN_TTL_SECONDS = 30.0

def invalidate_check_fn_cache():
    # 配置变更后（如 hermes tools enable）手动清除缓存
```

### 线程安全

`ToolRegistry` 使用 `RLock` 保证读写安全。MCP 动态插拔工具时
会在工作线程中调 `register()` / `deregister()`，标记冲突用
generation counter 检测。

---

## 三、TOOLSETS —— 工具选择层

**路径**: `toolsets.py`

静态的「工具名 → 工具集合」映射。几个关键设计：

### _HERMES_CORE_TOOLS

所有平台共享的核心工具列表：web_search、terminal、read_file、write_file、
vision_analyze、memory、execute_code、delegate_task 等约 30 个。

### 工具集定义 + 组合

```python
TOOLSETS = {
    "web": {
        "description": "Web research tools",
        "tools": ["web_search", "web_extract"],
        "includes": []
    },
    "debugging": {
        "description": "Debugging toolkit",
        "tools": ["terminal", "process"],
        "includes": ["web", "file"]  # 递归组合
    },
    "hermes-cli": {
        "description": "CLI toolset",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },
}
```

### resolve_toolset()

递归展开 includes，带循环检测：
- `resolve_toolset("debugging")` → `[process, terminal, web_extract, web_search, read_file, write_file, ...]`
- `resolve_toolset("all")` 或 `"*"` → 全部工具集的并集

### 回退到 Registry

`get_toolset()` 找不到静态定义时，自动回退到 registry 查询：
- Plugin 注册的工具集（`registry.get_tool_names_for_toolset()`）
- MCP 服务器的工具（通过 `register_toolset_alias()` 映射）

---

## 四、Memory —— 冻结快照模式

**路径**: `tools/memory_tool.py`

最关键的模式：**system prompt 用快照，工具响应传递实时状态**。

### 双存储

| 文件 | 用途 | 限长 |
|------|------|------|
| `MEMORY.md` | Agent 个人笔记（环境事实、项目约定） | 2200 字符 |
| `USER.md` | 用户画像（偏好、习惯、期望） | 1375 字符 |

### 冻结快照生命周期

```
Session 启动
    │
    ├─ MemoryStore.load() → 读文件 → 拍快照 (for_system_prompt())
    │
    ├─ System prompt 注入快照（此后不变）
    │
    └─ Agent 运行
         │
         ├─ 调用 memory tool (add/replace/remove)
         │    ├─ 立即写入磁盘文件（持久化）
         │    └─ 返回完整 entries 列表 → 通过 conversation history
         │      传递给 LLM（短时记忆可见）
         │
         └─ 下次 Session 启动 → 重新拍快照（包含上次写入）
```

**为什么不是实时更新 system prompt？**
- 保持 prompt cache 前缀不变，节省 API 成本
- LLM 从 tool response 里的完整 entries 列表已经能看到最新状态
- 不需要重建 system prompt

### 工具响应携带完整状态

```python
def _success_response(target, entries, limit, current):
    return {
        "success": True, "target": target,
        "entries": entries,   # 实时完整列表！
        "usage": f"{pct}% — {current:,}/{limit:,} chars",
        "entry_count": len(entries),
    }
```

LLM 每次调用 memory tool 后的 response 里都能读到最新 entries。

### 原子写入

```python
def _flush(self):
    fd, tmp = tempfile.mkstemp(dir=str(self._dir))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ENTRY_DELIMITER.join(self._entries))
        os.fsync(f.fileno())       # 保证数据落盘
    os.replace(tmp, file)          # POSIX 原子替换
```

---

## 五、System Prompt 组装

**路径**: `run_agent.py:4747` (`_build_system_prompt()`)
**辅助**: `agent/prompt_builder.py`

### 7 层结构 (按顺序)

| 层 | 来源 | 说明 |
|---|------|------|
| 1. 身份 | SOUL.md / DEFAULT_AGENT_IDENTITY | 人格设定 |
| 2. 帮助指引 | HERMES_AGENT_HELP_GUIDANCE | 关于 Hermes 本身的问题指向技能 |
| 3. 行为指引 | MEMORY_GUIDANCE / SESSION_SEARCH_GUIDANCE / SKILLS_GUIDANCE | 按工具有无条件注入 |
| 4. 执行纪律 | TOOL_USE_ENFORCEMENT_GUIDANCE / OPENAI_MODEL_EXECUTION_GUIDANCE | 模型特定行为约束 (GPT/Codex/Gemini) |
| 5. 记忆快照 | MemoryStore.for_system_prompt() | 冻结的快照 |
| 6. 技能索引 | build_skills_system_prompt() | 可用技能目录 |
| 7. 上下文文件 | build_context_files_prompt() | AGENTS.md / CLAUDE.md / SOUL.md |
| 8. 时间戳 | 当前时间 + Session ID + Model | 实时信息 |

### Context 文件优先级

```
.hermes.md / HERMES.md (git root 内搜索)  ← 最高
    ↓ (未找到)
AGENTS.md / agents.md (cwd only)
    ↓ (未找到)
CLAUDE.md / claude.md (cwd only)
    ↓ (未找到)
.cursorrules + .cursor/rules/*.mdc (cwd only)
```

每个 context 源 **上限 20,000 字符**，超过则 head/tail 截断（70%/20% 比例）。

### Prompt Injection 检测

所有 context 文件加载时扫描 11 种威胁模式：
- `ignore (previous|all|above|prior) instructions`
- `do not tell the user`
- `system prompt override`
- 不可见 Unicode 字符
- `display: none` 隐藏 div
- 凭证泄露模式

命中任一模式 → 整块内容被屏蔽（不注入 system prompt）。

---

## 六、ReAct 循环

**路径**: `run_agent.py:10098` (`run_conversation()`)

### 流程

```
run_conversation(user_message):
    1. 构建/复用缓存的 system prompt
    2. 追加 user message 到 messages
    3. 预检 context 是否超限 → 触发压缩
    4. 进入迭代循环 (max_iterations=90):
        a. 构建 API 请求 (messages + tools)
        b. 调用 LLM
        c. 解析 response:
            - 有 tool_calls → dispatch → 结果追加 → 继续
            - 无 tool_calls → 返回最终回复
        d. 错误处理:
            - JSON 解析失败 → 重试 (最多 5 次)
            - 空回复 → 重试 (最多 3 次)
            - 工具结果过大 → 截断
            - 截断后仍超限 → context 压缩
    5. 结束或达到 max_iterations
    6. 写入 session DB + 日志
```

### 错误恢复策略

| 异常 | 重试次数 | 策略 |
|------|---------|------|
| 无效 JSON | 5 | 告知模型格式错误 |
| 空内容 | 3 | 退避重试 |
| tool 参数缺失 | 5 | 告知模型补充参数 |
| 结果超限 | - | 截断到 max_result_size_chars |
| Context 超限 | - | 自动压缩 (中间轮次摘要) |

### Context 压缩

当消息列表超过模型 context window 阈值时：
1. 保护前 N 轮 + 后 N 轮
2. 中间轮次用 Gemini Flash 生成摘要
3. 创建子 session (parent_session_id 链)
4. 重建 system prompt 缓存

---

## 七、沙盒环境 —— 命令执行

**路径**: `tools/environments/base.py`, `local.py`, `docker.py`

策略模式：`BaseEnvironment` ABC → `LocalEnvironment` / `DockerEnvironment` / `ModalEnvironment` 等。

### Spawn-per-call 模型

每次命令都启动新的 `bash -c` 子进程（非长驻 shell）。

```
init_session():     捕获环境快照 (env vars, aliases, functions)
                         ↓
_wrap_command():    将命令包装为 source 快照 + set -e + cd + eval
                         ↓
execute():          最终子进程调用
```

### 环境快照机制

`init_session()` 导出当前 bash 环境为可 source 的脚本：

```python
def init_session(self):
    script = self._encode_script([
        # 导出变量（排除块名单）
        'export -p | grep -v ...',
        # 导出函数
        'export -f',
        # 导出声明的别名
        'alias',
    ])
    self._session_script = result
```

每次执行命令前 source 这个快照，保证新 shell 和初始化时状态一致。

### CWD 跟踪

- 本地：`/tmp/hermes_cwd_{pid}` 文件
- 远程（Docker/SSH）：stdout 中的 `__HERMES_CWD__:path` 标记

### 凭证剥离

```python
# 硬编码的黑名单 — 任何子进程都看不到这些变量
_HERMES_PROVIDER_ENV_BLOCKLIST = {
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_TOKEN",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_API_KEY", "FAL_KEY", "FIRECRAWL_API_KEY",
    ...
}
```

技能可以通过 `env_passthrough.py` 声明需要透传的变量（`required_environment_variables`），
但 Hermes 自己的 provider 凭证被硬编码拒绝（阻止 GHSA-rhgp-j443-p4rf 绕过）。

### Docker 沙盒安全参数

```python
"--cap-drop=ALL",           # 删除所有 Linux capabilities
"--security-opt", "no-new-privileges:true",
"--pids-limit", "100",      # 防止 fork 炸弹
"--tmpfs", "/tmp:noexec,nosuid,size=64m",
"--tmpfs", "/run:noexec,nosuid,size=32m",
```

---

## 八、危险命令审批

**路径**: `tools/approval.py`

### 三层防御

| 层 | 模式数 | 行为 |
|---|--------|------|
| HARDLINE_PATTERNS | 无条件阻止 | rm -rf /, mkfs.\*, dd if=/dev/zero of=/dev/sda |
| DANGEROUS_PATTERNS | 47 个 | 需要用户确认 |

### DANGEROUS_PATTERNS 覆盖

| 类别 | 示例模式 |
|------|---------|
| 递归删除 | `rm -r[^s]*/`, `rm -rf /` |
| 权限修改 | `chmod 777`, `chown -R` |
| 系统管理 | `apt remove`, `apt purge`, `systemctl disable` |
| 包管理器 | `pip uninstall`, `npm uninstall`, `gem uninstall` |
| 网络注入 | `iptables`, `ufw`, `tcpdump` |
| 密码暴露 | `chpasswd`, `passwd`, `echo root:...\|chpasswd` |
| 远程操作 | `git push --force`, `git push origin +main` |
| Shell 注入 | `curl.* | bash`, `curl.* | sh`, `wget.* -O- | sh` |
| Docker 逃逸 | `docker run --privileged`, `--cap-add` |
| 写凭证文件 | `.env` 写入, `~/.ssh/` 写入 |

### 交互审批

用户选项：
- `y` / `yes` — 仅本次允许
- `s` / `session` — 本次 session 内始终允许
- `a` / `always` — 永久加入白名单（写入 config.yaml）

---

## 九、Session 持久化

**路径**: `hermes_state.py`

SQLite 存储，WAL 模式，FTS5 全文搜索。

### 表结构

- `sessions` — 会话元数据（model, source, token counts, cost, title）
- `messages` — 完整消息历史（role, content, tool_calls, reasoning）
- `sessions_fts` / `sessions_fts_trigram` — FTS5 搜索索引

### 关键设计

| 设计 | 说明 |
|------|------|
| WAL 模式 | 多线程并发读 + 单写 |
| 写重试 | 随机 20-150ms 退避，防 convoy 效应 |
| 声明式 schema 演进 | 启动时自动 ADD COLUMN，无需版本迁移脚本 |
| FTS5 双索引 | unicode61 (英文) + trigram (CJK 子串) |
| Compression 链 | parent_session_id 链，get_compression_tip() 寻找最新延续 |

---

## 十、日志系统

**路径**: `hermes_logging.py`

### 文件布局

```
~/.hermes/logs/
├── agent.log    # INFO+, 主日志 (RotatingFile, 5MB×3)
├── errors.log   # WARNING+, 快速排查
└── gateway.log  # INFO+, gateway 组件 (仅 gateway 模式)
```

### 关键特性

| 特性 | 实现 |
|------|------|
| Session 标记 | 自定义 LogRecord factory 注入 `[session_id]` |
| 秘密脱敏 | `RedactingFormatter` 正则替换 API key 模式 |
| 组件路由 | `_ComponentFilter` 按 logger 名前缀分流 |
| 旋转日志 | `RotatingFileHandler` 防止无限增长 |

### 日志中的凭证保护

`RedactingFormatter` 在日志输出前扫描敏感模式：

```python
REDACT_PATTERNS = [
    (r'api[-_]?key[-_]?[\s"\'=]+[A-Za-z0-9+/]{20,}', "api_key"),
    (r'Bearer\s+[\w.-]+', "bearer_token"),
    (r'sk-[A-Za-z0-9]{20,}', "openai_key"),
    ...
]
```

### 示例日志行

```
2026-05-28 14:32:01 INFO [sess_abc123] tools.terminal: command completed (2.3s, 847 chars)
2026-05-28 14:32:01 INFO [sess_abc123] run_agent: tool web_search completed (1.2s, 15203 chars)
2026-05-28 14:32:05 WARNING [sess_abc123] tools.browser: browser not installed
```

---

## 十一、工具示例：几个关键实现的模式

### Terminal Tool

注册时附带 `check_fn`：
```python
def check_terminal_requirements():
    return True  # 始终可用

registry.register(
    name="terminal",
    toolset="terminal",
    schema=TERMINAL_SCHEMA,
    handler=handle_terminal,
    check_fn=check_terminal_requirements,
)
```

Handler 内部调用环境沙盒：
```python
def handle_terminal(args):
    cmd = args["command"]
    result = environment.execute(cmd)
    return json.dumps(result)
```

### Memory Tool

特殊处理——handler 不直接注册，而是在 agent 循环内联：
```python
if name == "memory":
    result = self._memory_store.add(content)
    # 不走 registry.dispatch()
```

### Skills Tool

`check_fn` 扫描文件系统：
```python
def check_skills_available():
    skills_dir = get_skills_dir()
    return skills_dir.exists() and any(skills_dir.iterdir())
```

`handler` 在调用时读取（非 import 时扫描）：
```python
def skills_list():
    return json.dumps(_find_all_skills())
```

---

## 十二、启动流程

```
CLI main() → cli.py
   │
   ├─ 加载配置 (config.yaml → HermesState)
   ├─ 安装日志系统 (setup_logging)
   ├─ 创建 SessionDB (SQLite, create_session)
   │
   ├─ 导入工具模块 (discover_builtin_tools)
   │   └─ 自动遍历 tools/*.py → 每个模块调 registry.register()
   │
   ├─ 创建 AIAgent
   │   ├─ 解析 enabled_toolsets / disabled_toolsets
   │   ├─ resolve_toolset() → 工具名列表
   │   ├─ registry.get_definitions() → OpenAI schema
   │   └─ _build_system_prompt() → 最终 system prompt
   │
   └─ 进入交互循环
       └─ run_conversation(user_input) → ReAct loop
```

---

## 十三、最小化实现模式总结

| 模式 | 说明 | Hermes 实现 |
|------|------|------------|
| **自注册工具** | 工具文件 import 时自动注册，Agent 无感知 | `registry.register()` at module level |
| **冻结快照** | System prompt 在 session 开始时固定，不因写入而重建 | `MemoryStore.load()` → `for_system_prompt()` |
| **TTL 缓存** | check_fn 结果缓存 30 秒，平衡实时性和重复调用 | `_check_fn_cached()` |
| **递归工具集** | TOOLSETS 可包含子集，支持组合 | `resolve_toolset()` + visited set |
| **Spawn-per-call** | 每次命令新 shell，无长驻进程 | `BaseEnvironment._run_bash()` |
| **Env 快照** | init 时捕获环境，每次执行前 source，保证一致性 | `init_session()` |
| **凭证剥离** | 硬编码黑名单 + 技能声明 allowlist | `_HERMES_PROVIDER_ENV_BLOCKLIST` |
| **模式审批** | 正则匹配 + 交互确认 + session/permanent 白名单 | `DANGEROUS_PATTERNS` |
| **声明式 Schema** | DDL 是唯一真相，启动时自动 ADD COLUMN | `_reconcile_columns()` |
| **WAL + 退避** | 多进程并发写，随机退避防止 convoy | `_execute_write()` |
| **Prompt 注入检测** | Context 文件加载前扫描威胁模式 | `_scan_context_content()` |
| **Head/tail 截断** | 大文件保头保尾，中间截断 | `_truncate_content()` |
| **Frozen prompt cache** | System prompt 一次构建、全程复用 | `_cached_system_prompt` |

---

## 十四、Credits

从 Hermes 学到的核心思路：
- **不给模型喂代码**，而是给工具让它自己去找
- **System prompt 分层**，按工具有无条件注入
- **快照 + 响应**组合解决记忆实时性
- **沙盒剥离凭证 + 交互审批**双层安全
- **单例 Registry** 解耦工具定义和使用

参考版本: Hermes v0.11.0 (2026-05)
