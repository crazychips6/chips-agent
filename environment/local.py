"""LocalEnvironment — 本地子进程执行

依赖关系：
- safety/approval.py    → 危险命令审批
- safety/sanitize.py    → 凭证剥离
- environment/base.py   → Environment Protocol

不依赖 agent/ 或 tool/ 模块。"""

import subprocess

from safety.approval import check, ApprovalAction
from safety.sanitize import strip_env
from environment.base import Environment, ExecuteResult


class LocalEnvironment(Environment):
    """在本地子进程中执行 shell 命令。

    Args:
        interactive: 是否启用交互审批。
        default_timeout: 命令默认超时秒数。
    """

    def __init__(self, interactive: bool = True, default_timeout: int = 30):
        self._interactive = interactive
        self._default_timeout = default_timeout

    def execute(self, command: str, timeout: int | None = None) -> ExecuteResult:
        """执行命令，带安全审批 + 凭证剥离。"""
        # 1. 安全审批
        result = check(command, interactive=self._interactive)
        if result.action == ApprovalAction.DENY:
            return ExecuteResult(
                returncode=-1,
                stdout="",
                stderr=f"安全拦截：{result.reason}",
            )

        # 2. 子进程执行（凭证剥离）
        timeout = timeout or self._default_timeout
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=strip_env(),
            )
            return ExecuteResult(
                returncode=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
            )
        except subprocess.TimeoutExpired:
            return ExecuteResult(
                returncode=-1,
                stdout="",
                stderr=f"命令执行超时 ({timeout}秒)",
            )
        except Exception as e:
            return ExecuteResult(
                returncode=-1,
                stdout="",
                stderr=f"执行错误: {e}",
            )

    def close(self) -> None:
        pass
