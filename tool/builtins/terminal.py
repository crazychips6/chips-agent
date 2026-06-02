"""terminal 工具 — 执行终端命令

模块级 _environment 引用由 cli.py wiring 时注入 Environment 实例。
工具本身不依赖 environment/ 或 safety/ 模块。"""

from tool.registry import registry

# wiring 时由 cli.py 注入
_environment = None


def _execute_handler(args) -> str:
    if _environment is None:
        return "错误：终端环境未初始化"

    command = args.get("command", "")
    if not command:
        return "错误：命令不能为空"

    timeout = args.get("timeout", 30)
    result = _environment.execute(command, timeout=timeout)

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
            "description": "在本地终端中执行一条 shell 命令，返回标准输出和标准错误",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
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
