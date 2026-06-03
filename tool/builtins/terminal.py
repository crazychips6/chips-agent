"""terminal 工具 — 执行终端命令

根据 CHIPS_ENV 环境变量在运行时选择后端环境：
- "local"（默认）：本地子进程
- "docker"：Docker 容器沙盒
首次调用时懒加载，后续复用缓存实例。"""

import os

from tool.registry import registry

_environment = None


def _get_environment():
    """首次调用时按当前 CHIPS_ENV 创建环境实例并缓存。"""
    global _environment
    if _environment is not None:
        return _environment

    env_type = os.environ.get("CHIPS_ENV", "local")
    if env_type == "docker":
        from environment.docker import DockerEnvironment

        image = os.environ.get("CHIPS_DOCKER_IMAGE", "alpine:latest")
        _environment = DockerEnvironment(image=image)
    else:
        from environment.local import LocalEnvironment

        _environment = LocalEnvironment(interactive=True)
    return _environment


def _execute_handler(args) -> str:
    env = _get_environment()
    command = args.get("command", "")
    if not command:
        return "错误：命令不能为空"

    timeout = args.get("timeout", 30)
    result = env.execute(command, timeout=timeout)

    parts = []
    if result.stdout:
        parts.append(result.stdout.rstrip("\n"))
    if result.stderr:
        parts.append(f"[stderr]\n{result.stderr.rstrip(chr(10))}")
    if result.returncode != 0:
        parts.append(f"→ 退出码: {result.returncode}")
    return "\n".join(parts) if parts else "(无输出)"


registry.register(
    name="terminal",
    toolset="core",
    schema={
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "在终端中执行一条命令，返回标准输出和标准错误",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的命令",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30",
                    },
                },
                "required": ["command"],
            },
        },
    },
    handler=_execute_handler,
)
