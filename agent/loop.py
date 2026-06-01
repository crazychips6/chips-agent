"""AIAgent — ReAct 循环"""

import json
import os
import re

from openai import OpenAI

# 移除 surrogate 字符（如 DeepSeek reasoning_content 中可能出现的 \udce4），
# 防止后续请求序列化时 UnicodeEncodeError: surrogates not allowed
_SURROGATE_RE = re.compile('[\ud800-\udfff]')


def _sanitize(text: str) -> str:
    return _SURROGATE_RE.sub("", text)

from agent.prompt import PromptBuilder
from memory.store import MemoryStore
from tool.registry import ToolRegistry

_DEBUG_LOG = os.path.join(os.path.dirname(__file__), "..", "log", "debug", "session.json")


class AIAgent:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        debug_context: bool = False,
        verbose: bool = False,
    ):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.debug_context = debug_context
        self.verbose = verbose
        self.prompt_builder = PromptBuilder(verbose=verbose)
        # registry / tool_names / memory 由外部注入，后续阶段改为构造参数注入
        self.registry: ToolRegistry | None = None
        self.tool_names: set[str] = set()
        self.memory: MemoryStore | None = None
        # 上下文文件列表，由 cli.py 在启动时搜索注入
        self.context_files: list[tuple[str, str, str]] = []
        # 当前轮次的对话消息历史，tool_calls 结果也会追加进来
        self.messages: list[dict] = []

    def _build_assistant_msg(self, msg) -> dict:
        """将 API 返回的 assistant 消息转为可追加到 self.messages 的 dict。

        单独抽离的原因：DeepSeek 的 reasoning_content 字段必须保留并在后续请求中
        原样回传，否则思考链会断裂。OpenAI SDK 的 msg 对象是只读的，需要转成普通 dict。
        """
        d = {"role": "assistant", "content": _sanitize(msg.content or "")}
        # DeepSeek 专有字段：非流式模式下通过 reasoning_content 返回思考链
        rc = getattr(msg, "reasoning_content", None)
        if rc:
            d["reasoning_content"] = _sanitize(rc)
        if msg.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": _sanitize(tc.function.name),
                        "arguments": _sanitize(tc.function.arguments),
                    },
                }
                for tc in msg.tool_calls
            ]
        return d

    def run_conversation(self, user_message: str, max_iterations: int = 20) -> str:
        # system prompt 每次重新构建，以便后续阶段支持动态上下文层
        memory_data = self.memory.get_all() if self.memory else {}
        tool_defs = self.registry.get_definitions(self.tool_names) if self.registry else []
        system = self.prompt_builder.build(
            memory=memory_data.get("memory", ""),
            user=memory_data.get("user", ""),
            context_files=self.context_files,
            tool_defs=tool_defs,
        )
        self.messages.append({"role": "user", "content": _sanitize(user_message)})

        # 每次 session 开始时创建目录并写空数组，清空上次内容
        if self.debug_context:
            os.makedirs(os.path.dirname(_DEBUG_LOG), exist_ok=True)
            with open(_DEBUG_LOG, "w") as f:
                json.dump([], f)

        rounds = [] if self.debug_context else None

        # ReAct 循环：工具调用 → 结果回填 → 继续，直到 LLM 返回纯文本回复
        for _ in range(max_iterations):
            kwargs = {
                "model": self.model,
                "messages": [{"role": "system", "content": system}, *self.messages],
                "max_tokens": 4096,
            }

            if self.registry:
                tools = self.registry.get_definitions(self.tool_names)
                if tools:
                    kwargs["tools"] = tools

            response = self.client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

            if self.debug_context:
                rounds.append({"request": kwargs, "response": {"content": msg.content, "reasoning_content": getattr(msg, "reasoning_content", None), "tool_calls": [{"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in (msg.tool_calls or [])]}})
                with open(_DEBUG_LOG, "w") as f:
                    json.dump(rounds, f, ensure_ascii=False, indent=2)

            if msg.tool_calls:
                # 先追加 assistant 消息（含 tool_calls），再逐个派发并将结果追加为 tool 消息
                self.messages.append(self._build_assistant_msg(msg))
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    result = self.registry.dispatch(tc.function.name, args)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
            else:
                content = msg.content or ""
                self.messages.append(self._build_assistant_msg(msg))
                return content

        # 达到最大迭代次数说明 LLM 可能陷入了工具调用死循环
        return f"已达到最大迭代次数 ({max_iterations})，对话可能不完整。"
