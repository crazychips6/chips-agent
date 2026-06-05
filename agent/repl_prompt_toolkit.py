"""prompt_toolkit 输入后端 — 多行编辑、历史持久化、语法高亮、tab 补全"""

import os

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style


class PromptToolkitInputBackend:
    """基于 prompt_toolkit 的输入后端，替代 StdioInputBackend。"""

    def __init__(self, prompt: str = "❯ ", history_path: str = ".chips/history", commands: list[str] | None = None):
        # 确保历史文件目录存在
        history_dir = os.path.dirname(history_path)
        if history_dir:
            os.makedirs(history_dir, exist_ok=True)

        self._style = Style.from_dict({
            "prompt": "bold ansicyan",
        })

        completer = None
        if commands:
            words = [f"/{name}" for name in commands]
            completer = WordCompleter(words, ignore_case=True, WORD=True)

        self._session = PromptSession(
            history=FileHistory(history_path),
            completer=completer,
            complete_while_typing=True,
            style=self._style,
            multiline=False,
            vi_mode=False,
        )

        self._prompt = prompt

    def read(self) -> str | None:
        try:
            return self._session.prompt(self._prompt, style=self._style)
        except (EOFError, KeyboardInterrupt):
            return None

    def close(self):
        pass
