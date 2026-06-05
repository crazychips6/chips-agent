"""REPL 循环 — 与具体 I/O 实现解耦"""

from typing import Callable


class StdioInputBackend:
    """基于 input() 的输入后端。"""

    def __init__(self, prompt: str = "> "):
        self._prompt = prompt

    def read(self) -> str | None:
        try:
            return input(self._prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return None

    def close(self):
        pass


class StdioOutputBackend:
    """基于 print() 的输出后端。"""

    def write(self, text: str):
        print(text)

    def close(self):
        pass


class CommandRegistry:
    """管理 /slash 命令。"""

    def __init__(self):
        self._commands: dict[str, tuple[Callable[[list[str]], str | None], str]] = {}
        self.should_exit = False

    def register(self, name: str, handler: Callable[[list[str]], str | None], description: str):
        self._commands[name] = (handler, description)

    @property
    def command_names(self) -> list[str]:
        return sorted({"exit", "help"} | self._commands.keys())

    def dispatch(self, text: str) -> str | None:
        """解析并执行 / 命令。

        返回 None = 不是命令，应交由 LLM 处理。
        返回 str = 命令已处理，可以输出给用户（空字符串表示无需输出）。
        """
        if not text.startswith("/"):
            return None

        parts = text.split(maxsplit=1)
        raw = parts[0].lower()
        # raw 是 "/exit" 或 "/help" 等，strip 掉 /
        cmd = raw[1:] if len(raw) > 1 else ""
        args = [parts[1]] if len(parts) > 1 else []

        if cmd == "exit":
            self.should_exit = True
            return ""
        if cmd == "help":
            return self._format_help()

        entry = self._commands.get(cmd)
        if entry is None:
            return f"未知命令: {raw}。输入 /help 查看可用命令。"

        handler, _ = entry
        return handler(args) or ""

    def _format_help(self) -> str:
        lines = ["可用命令:"]
        for name in sorted(self._commands):
            _, desc = self._commands[name]
            lines.append(f"  /{name:<10} {desc}")
        return "\n".join(lines)


class ReplLoop:
    """通用的 REPL 事件循环。"""

    def __init__(self, agent, input_backend, output_backend, cmd_registry: CommandRegistry):
        self.agent = agent
        self.input = input_backend
        self.output = output_backend
        self.cmd = cmd_registry

    def run(self):
        while not self.cmd.should_exit:
            text = self.input.read()
            if text is None:
                break
            text = text.strip()
            if not text:
                continue
            msg = self.cmd.dispatch(text)
            if msg is not None:
                if msg:
                    self.output.write(msg)
                continue
            reply = self.agent.run_conversation(text)
            if reply:
                self.output.write(reply)
        self.input.close()
        self.output.close()
