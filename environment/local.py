"""LocalEnvironment — 本地子进程执行

依赖关系：
- safety/approval.py    → 危险命令审批
- safety/sanitize.py    → 凭证剥离
- environment/base.py   → Environment Protocol

不依赖 agent/ 或 tool/ 模块。"""

import os
import shlex
import signal
import subprocess
import time

from safety.approval import check, ApprovalAction
from safety.audit import log_event
from safety.sanitize import strip_env
from environment.base import Environment, ExecuteResult


class LocalEnvironment(Environment):
    """在本地子进程中执行命令。

    无 shell 模式（shell=False），消除命令注入风险。
    跟踪所有子进程 PID，close() 时 SIGTERM → SIGKILL 清理。

    Args:
        interactive: 是否启用交互审批。
        default_timeout: 命令默认超时秒数。
    """

    def __init__(self, interactive: bool = True, default_timeout: int = 30):
        self._interactive = interactive
        self._default_timeout = default_timeout
        self._processes: list[subprocess.Popen] = []

    def execute(self, command: str, timeout: int | None = None) -> ExecuteResult:
        """执行命令，带安全审批 + 凭证剥离。"""
        # 1. 安全审批
        result = check(command, interactive=self._interactive)
        log_event("approval", {
            "action": result.action.value,
            "reason": result.reason or "safe",
            "command_truncated": command[:120],
        })
        if result.action == ApprovalAction.DENY:
            return ExecuteResult(
                returncode=-1,
                stdout="",
                stderr=f"安全拦截：{result.reason}",
            )

        # 2. 命令解析（无 shell 模式）
        try:
            args = shlex.split(command)
        except ValueError as e:
            return ExecuteResult(
                returncode=-1,
                stdout="",
                stderr=f"命令解析错误: {e}",
            )
        if not args:
            return ExecuteResult(returncode=0, stdout="", stderr="")

        # 3. 子进程执行（凭证剥离 + PID 跟踪）
        timeout = timeout or self._default_timeout
        proc = None
        try:
            proc = subprocess.Popen(
                args,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=strip_env(),
            )
            self._processes.append(proc)

            stdout, stderr = proc.communicate(timeout=timeout)
            return ExecuteResult(
                returncode=proc.returncode,
                stdout=stdout or "",
                stderr=stderr or "",
            )
        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
                proc.wait()
            return ExecuteResult(
                returncode=-1,
                stdout="",
                stderr=f"命令执行超时 ({timeout}秒)",
            )
        except Exception as e:
            if proc:
                proc.kill()
                proc.wait()
            return ExecuteResult(
                returncode=-1,
                stdout="",
                stderr=f"执行错误: {e}",
            )
        finally:
            if proc is not None and proc in self._processes:
                self._processes.remove(proc)

    def close(self) -> None:
        """清理所有跟踪中的子进程。"""
        if not self._processes:
            return
        # SIGTERM — 优雅终止
        for proc in self._processes:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
        # 等待 0.5s 让进程响应 SIGTERM
        time.sleep(0.5)
        # SIGKILL — 强制终止残留进程
        for proc in self._processes[:]:
            if proc.poll() is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            proc.wait()
        self._processes.clear()
