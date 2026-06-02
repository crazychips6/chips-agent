## 重要：安全审批层（safety/approval.py）

实现三层危险命令检测机制：

- **HARDLINE_PATTERNS**（7 条）：rm -rf /、dd 覆写磁盘、fork 炸弹、mkfs、块设备直接写入、系统关机重启、chmod -R 000 / → 命中直接拒绝
- **DANGEROUS_PATTERNS**（9 条）：pipe to shell、rm -rf、sudo、eval、chmod 777、覆写 /etc、kill -9、破坏性 docker、git force push → 需要用户交互确认
- `check(command, interactive=True) → ApprovalResult` — 非交互模式危险命令直接拒绝

## 重要：凭证剥离与日志脱敏（safety/sanitize.py）

- `DEFAULT_ENV_BLOCKLIST` — 20 个默认敏感环境变量名
- `strip_env()` — 过滤环境变量，敏感值替换为 `***`
- `redact()` — 文本脱敏（sk-、ghp_、gho_、xox*、Bearer token 模式）
- `RedactingFormatter` — logging 格式化器自动脱敏

## 普通：Environment Protocol + LocalEnvironment

- `environment/base.py` — Environment Protocol（execute + close）
- `environment/local.py` — LocalEnvironment 使用 safety/approval 做审批 + safety/sanitize 做凭证剥离

## 普通：terminal 工具

- `tool/builtins/terminal.py` — 终端命令执行工具
- 模块级 `_environment` 引用由 cli.py wiring 时注入，遵守 tool/ 零依赖约束

## 细微：Wiring 更新

- `tool/builtins/__init__.py` — 导入 terminal 模块触发自注册
- `tool/toolsets.py` — core 工具集新增 terminal
- `agent/cli.py` — 初始化 LocalEnvironment，注入 terminal_tool._environment
- 更新 toolsets 测试：core 集预期新增 terminal
