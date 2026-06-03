## 重要：Phase 10 B1 — 子进程生命周期管理

- 重写 `environment/local.py`，解决两个安全问题

### 改动

**1. `shell=True` → `shlex.split()` + `shell=False`**
- 消除命令注入风险（恶意命令如 `"; rm -rf /"` 不再生效）
- 代价：失去 shell 管道/重定向/env 变量展开能力，命令需为可执行文件 + 参数形式
- 仍需 shell 功能时可通过 `python3 -c "..."` 或 `sh -c "..."` 显式调用

**2. `subprocess.run()` → `subprocess.Popen()` + PID 跟踪**
- `subprocess.run()` 在 `TimeoutExpired` 后不清理子进程 → 泄漏
- 改用 `Popen()` + `communicate(timeout=...)`，异常时 `kill()` + `wait()` 确保清理
- `_processes: list[Popen]` 跟踪所有未完成子进程
- `close()` 改为 SIGTERM → 0.5s 等待 → SIGKILL → `wait()` 完整清理

**3. 测试更新**
- `test_execute_stderr_captured`: `echo err >&2` → `python3 -c "sys.stderr.write('err')"`
- `test_execute_env_stripped`: `echo $VAR` → `python3 -c "os.environ.get(...)"`
- 新增 4 个测试：无效语法、超时后清理、进程跟踪清空、多次执行无泄漏
- 全量测试从 214 → 218 通过
