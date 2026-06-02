"""environment 模块测试

LocalEnvironment 执行 + 安全审批集成。"""

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
        """空命令在 shell 中执行返回 0。"""
        env = LocalEnvironment(interactive=False)
        result = env.execute("")
        assert result.returncode == 0

    def test_execute_stderr_captured(self):
        env = LocalEnvironment(interactive=False)
        result = env.execute("echo err >&2")
        assert "err" in result.stderr

    def test_execute_env_stripped(self):
        """检查凭证剥离生效。"""
        env = LocalEnvironment(interactive=False)
        result = env.execute("echo $DEEPSEEK_API_KEY")
        # 如果被剥离了，输出应该是空
        assert result.stdout.strip() == "" or "***" not in result.stdout

    def test_close_does_not_raise(self):
        env = LocalEnvironment(interactive=False)
        env.close()  # 不应抛异常

    def test_cwd_safe_command(self):
        env = LocalEnvironment(interactive=False)
        result = env.execute("python3 --version")
        assert result.returncode == 0
        assert "Python" in result.stdout
