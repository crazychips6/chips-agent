"""Environment Protocol — 沙盒环境接口

定义环境抽象协议，供 agent 和工具通过构造注入使用。
具体实现（LocalEnvironment、DockerEnvironment 等）在独立模块中。"""

from typing import Protocol
from dataclasses import dataclass


@dataclass
class ExecuteResult:
    returncode: int
    stdout: str
    stderr: str


class Environment(Protocol):
    """沙盒环境协议。

    execute() 执行命令并返回结果。
    close()   清理资源。
    """

    def execute(self, command: str, timeout: int | None = None) -> ExecuteResult:
        ...

    def close(self) -> None:
        ...
