"""environment 模块测试

LocalEnvironment 执行 + 安全审批集成。"""

import subprocess
import time

import pytest

from environment.local import LocalEnvironment


class TestLocalEnvironment:
    def test_execute_safe_command(self):
        env = LocalEnvironment(interactive=False)
        result = env.execute("echo hello")
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_execute_hardline_denied(self):
        env = LocalEnvironment(interactive=False)
        result = env.execute("rm -rf /")
        assert result.returncode == -1
        assert "安全拦截" in result.stderr

    def test_execute_dangerous_denied_noninteractive(self):
        env = LocalEnvironment(interactive=False)
        result = env.execute("sudo ls")
        assert result.returncode == -1
        assert "安全拦截" in result.stderr

    def test_execute_nonexistent_command(self):
        env = LocalEnvironment(interactive=False)
        result = env.execute("nonexistent_command_xyz_123")
        assert result.returncode != 0

    def test_execute_with_custom_timeout(self):
        env = LocalEnvironment(interactive=False)
        result = env.execute("echo hi", timeout=5)
        assert result.returncode == 0

    def test_execute_empty_command(self):
        """空命令直接返回 0，不启动子进程。"""
        env = LocalEnvironment(interactive=False)
        result = env.execute("")
        assert result.returncode == 0
        assert result.stdout == ""

    def test_execute_stderr_captured(self):
        env = LocalEnvironment(interactive=False)
        result = env.execute("python3 -c \"import sys; sys.stderr.write('err')\"")
        assert "err" in result.stderr

    def test_execute_env_stripped(self):
        """检查凭证剥离生效。"""
        env = LocalEnvironment(interactive=False)
        result = env.execute("python3 -c \"import os; print(os.environ.get('DEEPSEEK_API_KEY', 'N/A'))\"")
        assert result.stdout.strip() == "N/A"

    def test_close_does_not_raise(self):
        env = LocalEnvironment(interactive=False)
        env.close()  # 不应抛异常

    def test_cwd_safe_command(self):
        env = LocalEnvironment(interactive=False)
        result = env.execute("python3 --version")
        assert result.returncode == 0
        assert "Python" in result.stdout

    def test_execute_invalid_syntax(self):
        """shlex 解析失败时返回错误。"""
        env = LocalEnvironment(interactive=False)
        result = env.execute("echo 'unterminated")
        assert result.returncode == -1
        assert "解析错误" in result.stderr

    def test_close_kills_timed_out_process(self):
        """close() 杀死超时后残留的子进程。"""
        env = LocalEnvironment(interactive=False)
        # 启动一个长时间运行的进程，但在它完成前就 close
        result = env.execute("python3 -c \"import time; time.sleep(10)\"", timeout=1)
        assert result.returncode == -1
        assert "超时" in result.stderr
        # close() 不应抛异常（即使有残留进程）
        env.close()

    def test_process_tracking_empty_after_success(self):
        """成功的执行后进程跟踪列表为空。"""
        env = LocalEnvironment(interactive=False)
        env.execute("echo ok")
        assert len(env._processes) == 0

    def test_multiple_sequential_executes(self):
        """多次执行不泄漏进程跟踪。"""
        env = LocalEnvironment(interactive=False)
        for _ in range(5):
            env.execute("echo hello")
        assert len(env._processes) == 0
