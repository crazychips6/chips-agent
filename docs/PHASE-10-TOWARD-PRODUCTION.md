# chips v0.2 → 脱离 Toy 阶段开发计划

## 现状

当前 chips 在接口设计上学习了 Hermes 的分层和解耦，但实现层面大量是"just enough to demo"的水平。以下计划围绕**最影响可用性**的短板展开。

---

## 阶段 A：Agent 核心可靠性 ⭐ 最高优先级

### A1 — LLM 调用链路容错

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/loop.py` | 单次同步调用，失败直接抛异常 | Retry（指数退避 + jitter）+ rate-limit 排队 |
| `agent/loop.py` | 无 streaming | Streaming 模式下逐 chunk 输出到终端 |
| `agent/loop.py` | 达到 20 次迭代粗暴截断 | 语义检测工具死循环，优雅降级 |

**参考**: Hermes `retry_utils.py` + `rate_limit_tracker.py` + `agent_loop.py` streaming 逻辑

### A2 — 上下文压缩保护

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/loop.py:_maybe_trim_context` | FIFO 删最早消息，可能砍 tool_call chain | 保护首尾 + 压缩中间 tool 结果，不丢 assistant/tool 配对 |

**参考**: Hermes `trajectory_compressor.py` 压缩策略

---

## 阶段 B：环境层硬化 ⭐ 高优先级

### B1 — 子进程生命周期管理

| 模块 | 现状 | 目标 |
|------|------|------|
| `environment/local.py` | `close()` 是 `pass`，子进程泄漏 | 跟踪所有子进程 PID，`close()` 时 SIGTERM → SIGKILL |
| `environment/local.py` | `shell=True` 命令注入风险 | 用 `shlex.split()` + 无 shell 模式安全执行 |

### B2 — DockerEnvironment

| 模块 | 现状 | 目标 |
|------|------|------|
| `environment/docker.py` | 不存在 | Docker SDK 启动临时容器，`pip install` / `apt` 等操作安全隔离 |

**参考**: Hermes `environments/hermes_base_env.py` — 支持 local/docker/singularity/ssh 多种后端

---

## 阶段 C：路径安全重写 ⭐ 高优先级

| 模块 | 现状 | 目标 |
|------|------|------|
| `tool/builtins/file.py` | `if pattern in abspath` 字符串匹配，误杀 + 绕过 | `Path.resolve().relative_to()` 白名单校验，符号链跟随 |

**参考**: Hermes `tools/path_security.py` + `tools/file_operations.py`

---

## 阶段 D：安全与可观测性

### D1 — 持久化审批白名单

| 模块 | 现状 | 目标 |
|------|------|------|
| `safety/approval.py` | 每次交互式审批，不记忆 | 用户批准的命令写入 `~/.chips/allowlist.yaml`，下次同命令自动通过 |

**参考**: Hermes `tools/approval.py` permanent allowlist

### D2 — 审计日志

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/loop.py` + `agent/cli.py` | 无审计 | 每次工具调用 + 审批决策写入只读审计表（`audit_log`），不可篡改 |

---

## 阶段 E：工具生态扩展

### E1 — 文件操作增强

| 工具 | 现状 | 目标 |
|------|------|------|
| `file_read` | 读整个文件 | 支持行范围读取、大文件分页 |
| `file_write` | 覆盖/追加 | 支持 patch（行级替换）、搜索替换 |
| 新增 | — | `file_search`（grep 封装） |

### E2 — Web 工具

| 工具 | 现状 | 目标 |
|------|------|------|
| 新增 | — | `web_fetch`（HTTP GET）、`web_search`（搜索 API） |

---

## 阶段 F：记忆与知识

### F1 — 记忆层级扩展

| 模块 | 现状 | 目标 |
|------|------|------|
| `memory/store.py` | 双文件读写 | working memory（当前会话）+ episodic（历史摘要）+ semantic（持久知识）三级 |
| 新增 | — | 会话结束时自动提取关键信息写入 episodic |

**参考**: Hermes `agent/memory_manager.py`

---

## 阶段 G：CLI 与 UX

### G1 — Rich REPL

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/cli.py` | 纯文本 `input()` + `print()` | rich 语法高亮、markdown 渲染、进度指示、/slash 命令 |

### G2 — Session 管理

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/cli.py` | `--resume` 参数 | 子命令：`chips session list`、`chips session show`、`chips session search` |

### G3 — 配置系统

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/cli.py` | 只读 `~/.chips/config.yaml`（2 个键） | `chips config set/get/list` 子命令，配置变更自动生效 |

---

## 阶段 H：多模态

### H1 — 图像理解支持

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/loop.py` | 纯文本消息 | 支持多模态模型的 image_url content block |

---

## 实施顺序建议

```
阶段 A ─── 阶段 B ─── 阶段 C ─── 阶段 D
                │                     │
                └── 阶段 E ─── 阶段 F ──┘
                                    │
                              阶段 G ─── 阶段 H
```

- **A + C** 解决最痛的可靠性/安全短板
- **B + D** 解决环境/可观测性
- **E + F + G** 扩展功能到"可日常使用"级别
- **H** 锦上添花

每个阶段的交付标准：**新增/修改代码有对应测试，全量测试通过，新增功能可手动验证**。
