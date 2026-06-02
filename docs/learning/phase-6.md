# Phase 6 — 安全审批 + 凭证剥离 + terminal tool

## 架构总结（个人梳理）

这次改动主要做了三件事：**安全审批**、**凭证剥离**、**terminal tool**。

### 各模块职责

```
safety/approval.py    无依赖模块            判断 shell 操作能不能执行
safety/sanitize.py    无依赖模块            脱敏 env，防止 shell 工具拿到涉密环境变量
environment/          导入 safety 模块       实现 shell 执行时的审批 + 脱敏
tool/builtins/terminal.py  零依赖            wiring 时通过指针指向 environment
```

### 调用链（正向）

```
1. registry.register("terminal", ...)
   → 给 terminal 工具注册 schema，agent 在 tool_defs 里感知到它

2. cli.py wiring:
   LocalEnvironment 实例化
   → terminal_tool._environment = env  （指针注入）

3. 运行时:
   LLM 调用 terminal
   → registry.dispatch("terminal", args)
   → terminal._execute_handler(args)
   → _environment.execute(command)
   → LocalEnvironment.execute():
       ├─ safety.approval.check(command)    // 审批
       ├─ subprocess.run(env=strip_env())   // 执行 + 脱敏
       └─ 返回 ExecuteResult → 回填 LLM
```

### 关键设计

- **approval 只针对 shell** — pattern 全是 shell 语法（`rm -rf`、`sudo`），不是通用审批
- **凭证剥离在 environment 层做** — `strip_env()` 在 `LocalEnvironment.execute()` 内部调用，terminal 工具不感知
- **指针注入支持多环境** — `_environment` 将来可以指向 `DockerEnvironment`、`SSHEnvironment` 等，terminal 工具代码不变
- **environment 的定位** — 作为唯一依赖 safety 的模块，负责把审批和脱敏串联到实际执行中
