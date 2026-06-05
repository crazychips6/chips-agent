# chips-agent 现状综述

> 版本: v0.3.0 | 测试: 343 条 | 开发周期: 2026-05-29 ~ 2026-06-05

---

## 一、整体规模

### 模块详情

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| `agent/` | 6 | ~500 | CLI 入口、ReAct 循环、Prompt 组装、REPL 框架 |
| `config/` | 3 | ~80 | 配置读写 (`~/.chips/config.yaml`) |
| `tool/` | 8 | ~700 | 注册中心、工具集、8 个内置工具 |
| `environment/` | 3 | ~350 | Environment Protocol、Local/Docker 执行 |
| `safety/` | 4 | ~300 | 危险命令审批、凭证剥离、白名单、审计日志 |
| `session/` | 3 | ~200 | SQLite 持久化、FTS5 全文搜索、CLI 管理 |
| `memory/` | 2 | ~150 | 三级记忆 (working/episodic/semantic) |
| `test/` | 15 | ~1,800 | 模块对应测试文件 |

### Git 统计

- 总提交数: 14
- 分支数: 2（main / phase-10）
- 首次提交: 2026-05-29
- 最新提交: 2026-06-05
- 开发周期: 8 天

---

## 二、已实现功能清单

### Agent 核心

| 功能 | 状态 | 说明 |
|------|------|------|
| ReAct 循环 | ✅ | tool_call → dispatch → 结果回填 → 继续 |
| 9 层 System Prompt | ✅ | 核心/日期/偏好/memory/user/episodic/working/上下文/工具/约定 |
| LLM Retry | ✅ | jittered 指数退避，可重试/不可重试异常分类 |
| Streaming 输出 | ✅ | 逐 chunk 打印，tool_calls 按 index 累积 |
| 工具死循环检测 | ✅ | 同工具同参 ≥4 次自动拦截 |
| 上下文压缩保护 | ✅ | 双阶段：压缩 tool 内容 → 原子组删除，保护首尾 |
| 注入检测 | ✅ | 7 种 prompt injection 正则匹配 |
| 上下文文件搜索 | ✅ | 向上搜索 CLAUDE.md / .claude/ |

### 工具系统

| 工具 | 集 | 功能 |
|------|----|------|
| `echo` | core | 回显，验证工具调用链路 |
| `terminal` | core | 执行命令（无 shell + 审批 + 凭证剥离） |
| `file_read` | core | 读文件（行范围、敏感路径拒绝） |
| `file_write` | core | 写/追加/patch 模式 |
| `file_search` | core | grep 封装，正则/文本模式 |
| `web_fetch` | core | HTTP GET，HTML 纯文本提取，SSRF 防护 |
| `web_search` | core | Tavily API / DuckDuckGo 回退 |
| `memory_read` | memory | 读取三级记忆 |
| `memory_write` | memory | 写入三级记忆 |

### 安全

| 功能 | 状态 | 说明 |
|------|------|------|
| 危险命令检测 | ✅ | HARDLINE(7条) + DANGEROUS(9条) 分层 |
| 交互审批 | ✅ | 弹窗 Y/N |
| 持久化白名单 | ✅ | `~/.chips/allowlist.yaml` |
| 凭证剥离 | ✅ | 20+ 环境变量模式 + 日志脱敏 |
| 审计日志 | ✅ | SQLite 哈希链防篡改 |
| 路径安全 | ✅ | `Path.resolve()` + `fnmatch`，白名单校验 |

### 执行环境

| 功能 | 状态 | 说明 |
|------|------|------|
| LocalEnvironment | ✅ | 无 shell、子进程跟踪 SIGTERM/SIGKILL |
| DockerEnvironment | ✅ | Docker SDK 启动临时容器 |

### 持久化

| 功能 | 状态 | 说明 |
|------|------|------|
| Session 自动保存 | ✅ | SQLite + WAL |
| FTS5 全文搜索 | ✅ | content-sync 触发器，中文 LIKE 回退 |
| Session 恢复 | ✅ | `--resume` / `--resume <id>` |
| 旋转日志 | ✅ | 5MB × 3 备份 |

### 记忆 (Phase 10 F1)

| 层级 | 存储 | 生命周期 |
|------|------|---------|
| Working | 内存 dict | 当前会话 |
| Episodic | EPISODIC.md | 跨会话（含时间戳） |
| Semantic | MEMORY.md + USER.md | 永久 |

### CLI

| 功能 | 状态 | 说明 |
|------|------|------|
| REPL 循环 | ✅ | ReplLoop + CommandRegistry 解耦 |
| prompt_toolkit | ✅ | 历史持久化、Tab 补全、多行编辑 |
| `chips config` | ✅ | set/get/list 子命令 |
| `chips session` | ✅ | list/show/search/delete 子命令 |

### 依赖

```toml
openai>=1.0.0
prompt_toolkit>=3.0.0
python-dotenv>=1.0
pyyaml>=6.0

# dev
pytest>=9.0.3
```

---

## 三、架构设计

### 分层依赖

```
tool/  safety/  session/  memory/  config/   ← 零内部依赖
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

---

## 四、测试覆盖

| 测试文件 | 测试数 |
|----------|--------|
| `test_registry.py` | 31 |
| `test_prompt.py` | 26 |
| `test_loop.py` | 24 |
| `test_builtins.py` | 24 |
| `test_approval.py` | 22 |
| `test_session.py` | 19 |
| `test_sanitize.py` | 14 |
| `test_environment.py` | 14 |
| `test_config.py` | 15 |
| `test_memory.py` | 12 |
| `test_toolsets.py` | 7 |
| `test_repl.py` | 16 |
| `test_cli.py` | 5 |
| `test_session_cli.py` | 8 |
| `test_docker_environment.py` | 10 |
| **总计** | **343** |

---

## 五、Phase 10 完成情况

| 编号 | 内容 | 状态 |
|------|------|------|
| A1 | LLM retry/streaming/死循环检测 | ✅ |
| A2 | 上下文压缩保护 | ✅ |
| B1 | 子进程生命周期管理 | ✅ |
| B2 | DockerEnvironment | ✅ |
| C | 路径安全重写 | ✅ |
| D1 | 持久化审批白名单 | ✅ |
| D2 | 审计日志 | ✅ |
| E1 | 文件操作增强 (patch/grep/行范围) | ✅ |
| E2 | Web 工具 | ✅ |
| F1 | 记忆层级扩展 | ✅ |
| G1 | Rich REPL | ✅ |
| G2 | Session 管理命令 | ✅ |
| G3 | 配置系统 | ✅ |

---

## 六、与 Hermes 的差距

| 维度 | chips | Hermes |
|------|-------|--------|
| 代码总量 | ~5,000 行 | ~541,000 行 |
| 工具数量 | 9 个 | 80+ 个 |
| 模型适配器 | 1 个 (OpenAI SDK) | 10+ 个 |
| 运行环境 | local/docker | local/docker/ssh/singularity |
| 多模态 | 不支持 | 支持图像 |
