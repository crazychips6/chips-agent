"""REPL 循环单元测试"""

from agent.repl import CommandRegistry, ReplLoop


class TestCommandRegistry:
    def test_non_command_returns_none(self):
        cmd = CommandRegistry()
        assert cmd.dispatch("hello") is None
        assert cmd.dispatch("") is None

    def test_help_builtin(self):
        cmd = CommandRegistry()
        cmd.register("ping", lambda _: "pong", "test command")
        result = cmd.dispatch("/help")
        assert result is not None
        assert "ping" in result
        assert "test command" in result

    def test_exit_sets_flag(self):
        cmd = CommandRegistry()
        assert not cmd.should_exit
        result = cmd.dispatch("/exit")
        assert cmd.should_exit
        assert result == ""

    def test_unknown_command(self):
        cmd = CommandRegistry()
        result = cmd.dispatch("/unknown")
        assert result is not None
        assert "未知命令" in result

    def test_registered_command(self):
        cmd = CommandRegistry()
        cmd.register("ping", lambda _: "pong", "")
        result = cmd.dispatch("/ping")
        assert result == "pong"

    def test_registered_command_with_args(self):
        cmd = CommandRegistry()
        cmd.register("echo", lambda args: " ".join(args), "")
        result = cmd.dispatch("/echo hello world")
        assert result == "hello world"

    def test_registered_command_no_output(self):
        cmd = CommandRegistry()
        cmd.register("noop", lambda _: None, "")
        result = cmd.dispatch("/noop")
        assert result == ""

    def test_command_names_property(self):
        cmd = CommandRegistry()
        cmd.register("bb", lambda _: "", "")
        cmd.register("aa", lambda _: "", "")
        cmd.register("cc", lambda _: "", "")
        assert cmd.command_names == ["aa", "bb", "cc", "exit", "help"]

    def test_command_names_empty(self):
        cmd = CommandRegistry()
        assert cmd.command_names == ["exit", "help"]

    def test_command_names_only_builtins(self):
        cmd = CommandRegistry()
        assert cmd.command_names == ["exit", "help"]


class TestReplLoop:
    def test_agent_called_for_non_command(self):
        agent = _MockAgent()
        inp = _BufInput(["hello", "/exit"])
        out = _BufOutput()
        cmd = CommandRegistry()
        loop = ReplLoop(agent, inp, out, cmd)
        loop.run()
        assert agent.calls == ["hello"]

    def test_agent_output_written(self):
        agent = _MockAgent(reply="some reply")
        inp = _BufInput(["hi", "/exit"])
        out = _BufOutput()
        cmd = CommandRegistry()
        loop = ReplLoop(agent, inp, out, cmd)
        loop.run()
        assert out.buf == ["some reply"]

    def test_empty_input_skipped(self):
        agent = _MockAgent()
        inp = _BufInput(["", "  ", "/exit"])
        out = _BufOutput()
        cmd = CommandRegistry()
        loop = ReplLoop(agent, inp, out, cmd)
        loop.run()
        assert agent.calls == []

    def test_command_not_sent_to_agent(self):
        agent = _MockAgent()
        inp = _BufInput(["/help", "/exit"])
        out = _BufOutput()
        cmd = CommandRegistry()
        cmd.register("test", lambda _: "ok", "")
        loop = ReplLoop(agent, inp, out, cmd)
        loop.run()
        assert agent.calls == []

    def test_eof_breaks(self):
        agent = _MockAgent()
        inp = _BufInput(["a"])  # 没有 /exit，但 None 会终止循环
        inp.return_none_after = True
        out = _BufOutput()
        cmd = CommandRegistry()
        loop = ReplLoop(agent, inp, out, cmd)
        loop.run()
        assert agent.calls == ["a"]

    def test_backends_closed_on_exit(self):
        agent = _MockAgent()
        inp = _BufInput(["/exit"])
        out = _BufOutput()
        cmd = CommandRegistry()
        loop = ReplLoop(agent, inp, out, cmd)
        loop.run()
        assert inp.is_closed
        assert out.is_closed


# ── Mock helpers ──


class _MockAgent:
    def __init__(self, reply: str = ""):
        self.calls = []
        self._reply = reply

    def run_conversation(self, text: str) -> str:
        self.calls.append(text)
        return self._reply


class _BufInput:
    def __init__(self, lines: list[str]):
        self._lines = list(lines)
        self.is_closed = False
        self.return_none_after = False

    def read(self) -> str | None:
        if self.return_none_after and not self._lines:
            return None
        if not self._lines:
            return None
        text = self._lines.pop(0)
        return text

    def close(self):
        self.is_closed = True


class _BufOutput:
    def __init__(self):
        self.buf = []
        self.is_closed = False

    def write(self, text: str):
        self.buf.append(text)

    def close(self):
        self.is_closed = True
