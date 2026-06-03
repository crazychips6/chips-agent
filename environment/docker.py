"""DockerEnvironment — Docker 容器沙盒执行

通过 docker CLI 与 Docker 守护进程通信，无需 Python Docker SDK。
容器在 __init__ 中启动，close() 时自动清理。"""

import shlex
import subprocess
import time

from environment.base import Environment, ExecuteResult


class DockerEnvironment(Environment):
    """在 Docker 容器内执行命令。

    自动管理容器生命周期：启动时创建后台容器，close() 时停止+删除。
    子进程（docker exec）被跟踪，超时时自动清理。

    Args:
        image: Docker 镜像名。
        default_timeout: 命令默认超时秒数。
    """

    def __init__(self, image: str = "alpine:latest", default_timeout: int = 30):
        self._image = image
        self._default_timeout = default_timeout
        self._container_id: str | None = None
        self._processes: list[subprocess.Popen] = []
        self._start_container()

    def _start_container(self) -> None:
        """启动后台容器（tail -f /dev/null 保持运行）。"""
        result = subprocess.run(
            ["docker", "run", "-d", "--rm", self._image, "tail", "-f", "/dev/null"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"启动 Docker 容器失败（镜像: {self._image}）: {result.stderr.strip()}"
                if result.stderr
                else f"启动 Docker 容器失败: 返回码 {result.returncode}"
            )
        self._container_id = result.stdout.strip()

    def execute(self, command: str, timeout: int | None = None) -> ExecuteResult:
        """在容器内执行命令，通过 docker exec 实现。"""
        if not self._container_id:
            return ExecuteResult(-1, "", "错误：容器未初始化")

        try:
            args = shlex.split(command)
        except ValueError as e:
            return ExecuteResult(-1, "", f"命令解析错误: {e}")
        if not args:
            return ExecuteResult(0, "", "")

        timeout = timeout or self._default_timeout
        docker_args = ["docker", "exec", self._container_id] + args
        proc = None
        try:
            proc = subprocess.Popen(
                docker_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
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
            return ExecuteResult(-1, "", f"命令执行超时 ({timeout}秒)")
        except Exception as e:
            if proc:
                proc.kill()
                proc.wait()
            return ExecuteResult(-1, "", f"执行错误: {e}")
        finally:
            if proc is not None and proc in self._processes:
                self._processes.remove(proc)

    def close(self) -> None:
        """清理容器和本地子进程。"""
        # 先杀本地跟踪中的子进程
        for proc in self._processes[:]:
            if proc.poll() is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            proc.wait()
        self._processes.clear()

        # 再杀并删除容器
        if self._container_id:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", self._container_id],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
            self._container_id = None
