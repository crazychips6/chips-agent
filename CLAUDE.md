# chips-agent 项目约定

## 资料库

- `docs/core-reference.md` — Hermes 蒸馏的最小 agent 部分，学习参考
- `docs/learning/` — 开发过程学习笔记，按阶段分文件存放
  - 如果用户说需要把学的内容进行记录，则优先记录到该文件夹中

## 通用规则（始终生效）

- **语言**：使用中文回复，技术术语不强行翻译
- **工具链**：python3 + uv 管理依赖和虚拟环境
- **可修改**：本文件允许修改

## 项目结构

```
agent/        # 核心：CLI入口 + ReAct循环 + prompt组装
tool/         # 工具系统：registry + toolsets + builtins/
safety/       # 安全层：危险命令审批 + 凭证剥离
environment/  # 沙盒：Environment协议 + LocalEnvironment
session/      # 持久化：SQLite (WAL + FTS5)
memory/       # 记忆：冻结快照 + 原子写
test/         # 测试：模块对应 test_*.py
docs/         # 架构 (ARCH.md) + 路线图 (ROADMAP.md) + 阶段计划 + 参考资料 + 学习笔记
log/coding_log/  # coding记录
```

## 触发规则

### 当下载依赖时

1. 检查本地镜像源：`pip config list` / `uv.toml` / 环境变量 `UV_*`
2. 有镜像 → 优先使用；镜像失败 → 回退默认源重试

### 当编写代码时

- 产生单元测试 → 直接写入 `/test/` 目录
- **合理添加注释**：注释说明 WHY（设计决策、约束条件、非显而易见的边界情况），不重复 WHAT（好的命名已表达的无需再注释）

### 当 coding 任务完成后

1. 在 `log/coding_log/` 写入记录
2. **不要自动提交代码** — 等待用户人工审核后，按指令执行 commit + push
3. 文件拆分规则：
   - 阶段任务（如 phase-N）或用户要求独立记录的改动 → 每个一个独立文件，命名 `phase-N-描述.md`
   - 其他细小改动 → 按日期合并到 `YYYY-MM-DD.md`
4. 每条记录格式（按重要程度分段）：
   ```
   ## 重要：<改动说明>
   - ...

   ## 普通：<改动说明>
   - ...

   ## 细微：<修正/补漏>
   - ...
   ```
5. 对需要审查的关键代码，可直接粘贴代码到 log
6. 日志文件总大小 ≤ 5MB，超限则删除最旧文件

### 编码约束

| 模块 | 允许依赖 | 禁止依赖 |
|------|---------|---------|
| `tool/` | 无 | 其他任何模块 |
| `safety/` | 无 | 其他任何模块 |
| `session/` | 无 | 其他任何模块 |
| `memory/` | 无 | 其他任何模块 |
| `environment/` | `safety/` | `agent/`, `tool/` |
| `agent/` | 所有模块的接口 | 具体实现类 |

- Agent 通过构造注入接收依赖，不直接 import 具体实现
- 工具通过 `registry.register()` 自注册，agent 不直接引用工具
