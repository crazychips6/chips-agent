# chips-agent 现状综述

> 版本: v0.1.0 | 代码量: ~3,400 行 Python | 测试: 185 条 | 开发周期: 2026-05-29 ~ 2026-06-03

---

## 一、整体规模

### 代码分布

| 分类 | 文件数 | 代码行数 | 占比 |
|------|--------|----------|------|
| 源模块 | 21 | 1,718 | 50% |
| 测试 | 12 | 1,703 | 50% |
| 配置/文档 | ~15 | ~1,300 | — |
| **总计** | **~48** | **~3,400** | **100%** |

### 模块详情

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| `agent/` | 4 | 625 | CLI 入口、ReAct 循环、Prompt 组装、日志 |
| `tool/` | 7 | 546 | 注册中心、工具集、6 个内置工具 |
| `environment/` | 2 | 98 | Environment Protocol、本地子进程执行 |
| `safety/` | 2 | 186 | 危险命令审批、凭证剥离与脱敏 |
| `session/` | 2 | 186 | SQLite 持久化、FTS5 全文搜索 |
| `memory/` | 2 | 68 | 冻结快照、原子写 |
| `test/` | 12 | 1,703 | 模块对应测试文件 |

### Git 统计

- 总提交数: 11
- 分支数: 3（main / develop / phase-10）
- 首次提交: 2026-05-29
- 最新提交: 2026-06-03
- 开发周期: 6 天

---

## 二、已实现功能清单

### Agent 核心

| 功能 | 状态 | 说明 |
|------|------|------|
| ReAct 循环 | ✅ | tool_call → dispatch → 结果回填 → 继续 |
| 7 层 System Prompt | ✅ | 核心身份/日期/偏好/记忆/上下文/工具/约定 |
| 上下文压缩 | ✅ | 字符阈值超限时裁剪历史（FIFO，保留最近 2 条） |
| 工具注入检测 | ✅ | 7 种 prompt injection 正则匹配 |
| 上下文文件搜索 | ✅ | 向上搜索 CLAUDE.md / CHIP.md / .claude/ |
| Memory 快照注入 | ✅ | MEMORY.md + USER.md 双文件 |

### 工具系统

| 工具 | 集 | 功能 |
|------|----|------|
| `echo` | core | 回显，验证工具调用链路 |
| `terminal` | core | 执行 shell 命令（带安全审批 + 凭证剥离） |
| `file_read` | core | 读文件，拒绝 .env/密钥/.chips 等 |
| `file_write` | core | 写/追加文件，拒绝 /etc/.git/ 等敏感路径 |
| `memory_read` | memory | 读取记忆 |
| `memory_write` | memory | 写入记忆 |

### 安全

| 功能 | 状态 | 说明 |
|------|------|------|
| 危险命令检测 | ✅ | HARDLINE(7条) + DANGEROUS(9条) 分层匹配 |
| 交互审批 | ✅ | 危险命令弹窗 Y/N |
| 凭证剥离 | ✅ | 20 种环境变量模式脱敏 |
| 日志脱敏 | ✅ | RedactingFormatter 脱敏 API key/Bearer token |
| 路径安全 | ✅（基础） | 文件工具敏感路径拒绝，字符串匹配级别 |

### 持久化

| 功能 | 状态 | 说明 |
|------|------|------|
| Session 自动保存 | ✅ | SQLite + WAL 模式 |
| FTS5 全文搜索 | ✅ | content-sync 触发器自动索引 |
| Session 恢复 | ✅ | `--resume` / `--resume <id>` |
| 旋转日志 | ✅ | 5MB × 3 备份 |

### 配置

| 功能 | 状态 | 说明 |
|------|------|------|
| `.env` 加载 | ✅ | python-dotenv |
| `~/.chips/config.yaml` | ✅ | model / base_url 自动加载 |
| CLI 参数 | ✅ | 9 个参数 |

### CLI 入口

| 参数 | 说明 |
|------|------|
| `--model` | 模型名称 |
| `--base-url` | API 地址 |
| `--message`, `-m` | 单次对话退出 |
| `--version` | 版本号 |
| `--debug-context` | 记录 LLM 请求/响应到 JSON |
| `--toolset` | 工具集选择 |
| `--no-memory` | 禁用记忆 |
| `--verbose` | 显示 prompt 各层 |
| `--resume` | 恢复会话 |

### 依赖

```toml
openai>=1.0.0
python-dotenv>=1.0
pyyaml>=6.0

# dev
pytest>=9.0.3
```

---

## 三、架构设计

### 分层依赖

```
tool/  safety/  session/  memory/         ← 零内部依赖
    ↕        ↕
environment/                                ← 仅依赖 safety
    ↕        ↕
agent/                                      ← 依赖接口而非实现
```

### 解耦原则

- **工具零依赖**: `tool/` 不 import 任何其他项目模块
- **构造注入**: AIAgent 通过 `__init__` / 属性注入接收依赖
- **自注册**: 工具通过 `registry.register()` 自注册，import 模块即生效
- **Environment Protocol**: 抽象接口，可替换实现

### 数据流

```
User Input → CLI → AIAgent.run_conversation()
                       │
                       ├─ PromptBuilder.build() → 7层 System Prompt
                       │
                       ├─ LLM API → response
                       │    ├─ 有 tool_calls → dispatch() → 回填
                       │    └─ 纯文本 → 返回用户
                       │
                       ├─ SessionDB.save_messages() (每个轮次)
                       └─ MemoryStore (仅在工具调用时写入)
```

---

## 四、测试覆盖

### 按模块

| 测试文件 | 测试数 | 测试内容 |
|----------|--------|----------|
| `test_registry.py` | 31 | 注册/派发/缓存/线程安全/业务场景 |
| `test_prompt.py` | 26 | 7 层组装/injection 检测/截断/上下文搜索 |
| `test_approval.py` | 22 | hardline/dangerous/交互审批/边界 |
| `test_builtins.py` | 21 | echo/memory/file/terminal 工具 |
| `test_session.py` | 19 | CRUD/FTS5/搜索/删除/边界 |
| `test_sanitize.py` | 14 | 凭证剥离/文本脱敏/格式化器 |
| `test_loop.py` | 11 | Assistant 消息构建/ReAct 流程/迭代上限 |
| `test_environment.py` | 10 | 命令执行/安全拦截/超时/凭证剥离 |
| `test_memory.py` | 10 | 读写/持久化/原子写/快照格式 |
| `test_toolsets.py` | 7 | 工具集解析/递归/循环引用 |
| `test_cli.py` | 5 | Wiring 集成/工具集组合 |
| **总计** | **176**（收集 185，含 parametrize 展开） | |

---

## 五、缺点分析

### 5.1 可靠性短板

| 问题 | 位置 | 影响 | 严重度 |
|------|------|------|--------|
| 无 retry | `agent/loop.py:117` | API 429/超时直接崩溃 | 🔴 |
| 无 streaming | `agent/loop.py` | 用户长时间无反馈 | 🔴 |
| 上下文压缩破坏 tool_call chain | `agent/loop.py:166` | 删除最早消息可能砍掉关键依赖 | 🔴 |
| 20 次迭代硬上限 | `agent/loop.py:79` | 长任务粗暴截断 | 🟡 |
| 单模型单 API | `agent/loop.py:117` | 无 fallback 模型 | 🟡 |

### 5.2 环境/进程管理

| 问题 | 位置 | 影响 | 严重度 |
|------|------|------|--------|
| `close()` 是 pass | `environment/local.py:71` | 子进程泄漏 | 🔴 |
| `shell=True` | `environment/local.py:47` | 命令注入风险 | 🔴 |
| 无 PTY | `environment/local.py` | 无法交互式命令 | 🟡 |
| 无 Docker 支持 | 不存在 | 无法安全隔离 | 🟡 |

### 5.3 安全缺陷

| 问题 | 位置 | 影响 | 严重度 |
|------|------|------|--------|
| 路径安全字符串匹配 | `tool/builtins/file.py:63` | `/etc` 和 `/etcetera` 都拦（误杀），符号链可绕过 | 🟡 |
| 无审批白名单 | `safety/approval.py` | 每次弹窗，用户疲劳后可能盲目允许 | 🟡 |
| 无审计日志 | 不存在 | 无法追溯谁执行了什么命令 | 🟡 |
| Injection 检测仅正则 | `agent/prompt.py` | 对抗性 prompt 轻松绕过 | 🟡 |

### 5.4 功能缺失

| 缺失功能 | 说明 | 优先级 |
|----------|------|--------|
| Web 工具 | 无 HTTP fetch / search | 🟡 |
| 文件搜索/替换 | 只有读写，无 grep/patch | 🟡 |
| 多级记忆 | 只有平面 key-value | 🟡 |
| Rich REPL | 纯文本 input/print | 🟢 |
| Session 管理子命令 | 只有 `--resume` 参数 | 🟢 |
| 配置子命令 | 手动改 YAML | 🟢 |
| 多模态 | 不支持图像 | 🟢 |

### 5.5 可观测性不足

| 问题 | 说明 | 严重度 |
|------|------|--------|
| 无用量统计 | 不知道 token 消耗、API 费用 | 🟡 |
| 无性能追踪 | 不知道每次 LLM 调用耗时 | 🟡 |
| 调试日志需手动开启 | `--debug-context` 才记录 | 🟢 |
| 无健康检查 | 无法判断 agent 是否正常工作 | 🟡 |

---

## 六、与 Hermes 的差距

| 维度 | chips | Hermes | 差距倍数 |
|------|-------|--------|----------|
| 代码总量 | ~3,400 行 | ~541,000 行 | ~160x |
| 工具数量 | 6 个 | 80+ 个 | ~13x |
| 模型适配器 | 1 个（OpenAI SDK） | 10+ 个 | 10x+ |
| 运行环境 | 本地子进程 | local/docker/ssh/singularity | 4x |
| CLI 复杂度 | 1 文件 ~155 行 | `hermes_cli/` ~50 文件 | 50x |
| Gateway | 无 | `gateway/` ~20 文件 | — |
| 插件系统 | 无 | `plugins/` + hooks | — |
| 技能生态 | 无 | skills hub + marketplace | — |
| ACP | 无 | `acp_adapter/` ~9 文件 | — |
| 测试 | 185 条 | 更多（具体未统计） | — |

---

## 七、总结

chips 是一个**架构设计正确、实现尚浅**的 agent harness。它的核心价值在于：

1. **干净的分层解耦** — 每层职责清晰，依赖方向单向，模块间通过接口通信
2. **完整的测试覆盖** — 50% 代码是测试，核心逻辑有充分验证
3. **可扩展的骨架** — 新增工具只需写 handler + register，新增 environment 只需实现 Protocol

但它离"可日常使用"还差 A 阶段的 **retry/streaming/上下文压缩**，离"可上线部署"还差 B 阶段的**进程管理和容器隔离**。这些正是 Phase 10 要解决的问题。
