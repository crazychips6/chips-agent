"""DockerEnvironment 测试

需要 Docker 守护进程可用，否则跳过。"""

import subprocess

import pytest

from environment.docker import DockerEnvironment


def _docker_available() -> bool:
    """检测 Docker 守护进程是否可用。"""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


docker_required = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker 守护进程不可用",
)


class TestDockerEnvironment:
    """DockerEnvironment 执行 + 生命周期。"""

    @docker_required
    def test_execute_echo(self):
        env = DockerEnvironment()
        result = env.execute("echo hello")
        assert result.returncode == 0
        assert result.stdout.strip() == "hello"
        env.close()

    @docker_required
    def test_execute_stderr(self):
        env = DockerEnvironment()
        result = env.execute("sh -c 'echo err >&2'")
        assert result.returncode == 0
        assert "err" in result.stderr
        env.close()

    @docker_required
    def test_execute_nonexistent_command(self):
        env = DockerEnvironment()
        result = env.execute("nonexistent_cmd_xyz")
        assert result.returncode != 0
        env.close()

    @docker_required
    def test_execute_timeout(self):
        """超时返回 -1 和超时提示。"""
        env = DockerEnvironment()
        result = env.execute("sleep 10", timeout=1)
        assert result.returncode == -1
        assert "超时" in result.stderr
        env.close()

    @docker_required
    def test_execute_empty_command(self):
        env = DockerEnvironment()
        result = env.execute("")
        assert result.returncode == 0
        assert result.stdout == ""
        env.close()

    @docker_required
    def test_execute_invalid_syntax(self):
        """shlex 解析失败不执行命令。"""
        env = DockerEnvironment()
        result = env.execute("echo 'unterminated")
        assert result.returncode == -1
        assert "解析错误" in result.stderr
        env.close()

    @docker_required
    def test_close_cleans_container(self):
        """close() 后容器被删除。"""
        env = DockerEnvironment()
        cid = env._container_id
        assert cid
        env.close()
        # 验证容器已不存在
        result = subprocess.run(
            ["docker", "inspect", cid],
            capture_output=True,
        )
        assert result.returncode != 0

    @docker_required
    def test_close_twice_no_error(self):
        """重复 close 不抛异常。"""
        env = DockerEnvironment()
        env.close()
        env.close()

    @docker_required
    def test_multiple_executes(self):
        """在同一容器中多次执行。"""
        env = DockerEnvironment()
        for _ in range(3):
            result = env.execute("echo ok")
            assert result.returncode == 0
            assert result.stdout.strip() == "ok"
        env.close()

    @docker_required
    def test_init_creates_container(self):
        """__init__ 后容器正在运行。"""
        env = DockerEnvironment()
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", env._container_id],
                capture_output=True, text=True,
            )
            assert result.stdout.strip() == "true"
        finally:
            env.close()
