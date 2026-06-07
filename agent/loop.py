"""AIAgent — ReAct 循环"""

import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from types import SimpleNamespace

import openai
from openai import OpenAI

# 移除 surrogate 字符（如 DeepSeek reasoning_content 中可能出现的 \udce4），
# 防止后续请求序列化时 UnicodeEncodeError: surrogates not allowed
_SURROGATE_RE = re.compile('[\ud800-\udfff]')


def _sanitize(text: str) -> str:
    return _SURROGATE_RE.sub("", text)

logger = logging.getLogger("chips.agent.loop")

from agent.message import ImageBlock, TextBlock, image_file_to_data_uri, parse_user_content, to_openai_messages
from agent.prompt import PromptBuilder
from agent.retry import jittered_backoff
from memory.manager import MemoryManager
from safety.audit import log_event
from session.db import SessionDB
from tool.registry import ToolRegistry

_DEBUG_LOG = os.path.join(os.path.dirname(__file__), "..", "log", "debug", "session.json")

# 同一工具+同参数签名重复 N 次视为死循环
_MAX_TOOL_LOOP = 4


class AIAgent:
    # 已知支持 vision 的模型列表，用于 ContentBlock 校验
    _VISION_MODELS: frozenset = frozenset({
        "gpt-4o", "gpt-4o-mini", "gpt-4o-2024-08-06", "gpt-4o-2024-05-13",
        "gpt-4o-mini-2024-07-18",
        "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620",
        "claude-3-opus-20240229",
        "gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash",
    })
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        debug_context: bool = False,
        verbose: bool = False,
        stream: bool = False,
        max_retries: int = 3,
    ):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.debug_context = debug_context
        self.verbose = verbose
        self.stream = stream
        self._max_retries = max_retries
        self.prompt_builder = PromptBuilder(verbose=verbose)
        # registry / tool_names / memory 由外部注入，后续阶段改为构造参数注入
        self.registry: ToolRegistry | None = None
        self.tool_names: set[str] = set()
        self.memory_manager = MemoryManager()
        # 上下文文件列表，由 cli.py 在启动时搜索注入
        self.context_files: list[tuple[str, str, str]] = []
        # 当前轮次的对话消息历史，tool_calls 结果也会追加进来
        self.messages: list[dict] = []
        # session 持久化，由 cli.py wiring 注入
        self.session_db: SessionDB | None = None
        self.session_id: str = ""
        self._saved_count: int = 0
        # 上下文压缩：消息总字符超限时裁剪历史
        self.max_context_chars: int = 100_000

    # ── LLM 调用（带重试） ──

    def _call_with_retry(self, fn, desc="LLM 调用") -> any:
        """调用 fn，遇可重试异常时退避重试。"""
        last_error = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return fn()
            except openai.BadRequestError as e:
                raise RuntimeError(f"请求参数错误（不重试）：{e}")
            except openai.RateLimitError:
                last_error = "API 速率限制"
                if attempt < self._max_retries:
                    delay = jittered_backoff(attempt)
                    logger.warning("llm_call retry attempt=%d/%d reason=%s", attempt, self._max_retries, last_error)
                    print(f"\n  [重试 {attempt}/{self._max_retries}] {last_error}，等待 {delay:.0f}s...", file=sys.stderr)
                    time.sleep(delay)
            except openai.APIStatusError as e:
                if e.status_code in (502, 503, 504):
                    last_error = f"服务暂时不可用 ({e.status_code})"
                    if attempt < self._max_retries:
                        delay = jittered_backoff(attempt, base_delay=2.0)
                        logger.warning("llm_call retry attempt=%d/%d reason=%s", attempt, self._max_retries, last_error)
                        print(f"\n  [重试 {attempt}/{self._max_retries}] {last_error}，等待 {delay:.0f}s...", file=sys.stderr)
                        time.sleep(delay)
                else:
                    raise RuntimeError(f"API 错误 (HTTP {e.status_code}，不重试)：{e}")
            except openai.APITimeoutError:
                last_error = "请求超时"
                if attempt < self._max_retries:
                    delay = jittered_backoff(attempt, base_delay=2.0)
                    logger.warning("llm_call retry attempt=%d/%d reason=%s", attempt, self._max_retries, last_error)
                    print(f"\n  [重试 {attempt}/{self._max_retries}] {last_error}，等待 {delay:.0f}s...", file=sys.stderr)
                    time.sleep(delay)
            except openai.APIConnectionError:
                last_error = "网络连接异常"
                if attempt < self._max_retries:
                    delay = jittered_backoff(attempt, base_delay=2.0)
                    logger.warning("llm_call retry attempt=%d/%d reason=%s", attempt, self._max_retries, last_error)
                    print(f"\n  [重试 {attempt}/{self._max_retries}] {last_error}，等待 {delay:.0f}s...", file=sys.stderr)
                    time.sleep(delay)
            except openai.BadRequestError as e:
                raise RuntimeError(f"请求参数错误（不重试）：{e}")
            except Exception as e:
                last_error = f"未知错误：{e}"
                if attempt < self._max_retries:
                    delay = jittered_backoff(attempt, base_delay=1.0)
                    print(f"\n  [重试 {attempt}/{self._max_retries}] {last_error}，等待 {delay:.0f}s...", file=sys.stderr)
                    time.sleep(delay)
        raise RuntimeError(f"{desc}失败（已重试 {self._max_retries} 次）：{last_error}")

    def _call_llm(self, kwargs) -> any:
        """非流式调用，带自动重试。"""
        _res = {}
        def _do_call():
            response = self.client.chat.completions.create(**kwargs)
            _res["response"] = response
            return response.choices[0].message
        t0 = time.time()
        msg = self._call_with_retry(_do_call)
        elapsed = int((time.time() - t0) * 1000)
        if "response" in _res:
            usage = _res["response"].usage
            pt = usage.prompt_tokens if usage else -1
            ct = usage.completion_tokens if usage else -1
            logger.info("llm_call model=%s stream=false duration_ms=%d prompt_tokens=%d completion_tokens=%d",
                         self.model, elapsed, pt, ct)
        return msg

    def _call_llm_streaming(self, kwargs) -> any:
        """流式调用，逐 chunk 输出，带自动重试。"""
        stream_kwargs = {**kwargs, "stream": True, "stream_options": {"include_usage": True}}
        _usage = {}

        def _do_stream():
            stream = self.client.chat.completions.create(**stream_kwargs)
            content = ""
            tool_calls: dict[int, dict] = {}

            for chunk in stream:
                if chunk.usage:
                    _usage["prompt"] = chunk.usage.prompt_tokens
                    _usage["completion"] = chunk.usage.completion_tokens
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if not delta:
                    continue

                if delta.content:
                    print(delta.content, end="", flush=True)
                    content += delta.content

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls:
                            tool_calls[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
                        if tc.id:
                            tool_calls[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls[idx]["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_calls[idx]["function"]["arguments"] += tc.function.arguments

            msg = SimpleNamespace()
            msg.content = content
            msg.reasoning_content = None

            if tool_calls:
                calls = []
                for i in sorted(tool_calls.keys()):
                    tc = tool_calls[i]
                    func = SimpleNamespace()
                    func.name = tc["function"]["name"]
                    func.arguments = tc["function"]["arguments"]
                    call = SimpleNamespace()
                    call.id = tc["id"]
                    call.type = "function"
                    call.function = func
                    calls.append(call)
                msg.tool_calls = calls
            else:
                msg.tool_calls = None
                print()  # 纯文本回复结束后换行

            return msg

        t0 = time.time()
        msg = self._call_with_retry(_do_stream, desc="流式 LLM 调用")
        elapsed = int((time.time() - t0) * 1000)
        pt = _usage.get("prompt", -1)
        ct = _usage.get("completion", -1)
        logger.info("llm_call model=%s stream=true duration_ms=%d prompt_tokens=%d completion_tokens=%d",
                     self.model, elapsed, pt, ct)
        return msg

    # ── 消息构建 ──

    def _build_assistant_msg(self, msg) -> dict:
        """将 API 返回的 assistant 消息转为可追加到 self.messages 的 dict。"""
        d = {"role": "assistant", "content": _sanitize(msg.content or "")}
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

    def _detect_tool_loop(self, tool_name: str, args_str: str) -> bool:
        """检测同一工具+同一参数是否被重复调用（死循环）。"""
        if not hasattr(self, "_tool_call_history"):
            self._tool_call_history = defaultdict(int)
        key = f"{tool_name}:{args_str}"
        self._tool_call_history[key] += 1
        return self._tool_call_history[key] >= _MAX_TOOL_LOOP

    # ── Vision 能力检测 ──

    def _is_vision_model(self) -> bool:
        """当前模型是否支持 vision。"""
        return self.model in self._VISION_MODELS

    def _check_vision_capability(self, api_messages: list[dict]):
        """检查消息中是否有图片，以及当前模型是否支持 vision。"""
        if self._is_vision_model():
            return
        for msg in api_messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                continue
            # content 是序列化后的 list[dict]
            if any(
                isinstance(block, dict) and block.get("type") == "image_url"
                for block in content
            ):
                raise ValueError(
                    f"消息包含图片但当前模型 {self.model} 不支持 vision。"
                    f"请使用 vision 模型，例如：gpt-4o、claude-3-5-sonnet"
                )

    # ── 自动图片注入 ──

    _IMAGE_PATH_PREFIX = "截图已保存到 "

    @staticmethod
    def _extract_screenshot_path(result: str) -> str | None:
        """从截图工具结果中提取文件路径。"""
        if not result.startswith(AIAgent._IMAGE_PATH_PREFIX):
            return None
        path = result[len(AIAgent._IMAGE_PATH_PREFIX):].split("（")[0]
        return path if os.path.isfile(path) else None

    def _maybe_inject_image(self, result: str):
        """如果工具结果是截图，将图片注入为 user message 供后续 LLM 调用。"""
        path = self._extract_screenshot_path(result)
        if not path:
            return
        try:
            data_uri = image_file_to_data_uri(path)
            self.messages.append({
                "role": "user",
                "content": [
                    TextBlock(text="这是截图，请分析："),
                    ImageBlock(url=data_uri),
                ],
            })
        except Exception:
            logger.warning("image_inject_failed path=%s", path, exc_info=True)

    # ── 主循环 ──

    def run_conversation(self, user_message: str, max_iterations: int = 20) -> str:
        # system prompt 每次重新构建
        mem_prompt = self.memory_manager.build_system_prompt()
        prefetch = self.memory_manager.prefetch_all(user_message)
        if prefetch:
            mem_prompt = (mem_prompt + "\n\n" + prefetch) if mem_prompt else prefetch
        system = self.prompt_builder.build(
            memory_prompt=mem_prompt,
            context_files=self.context_files,
        )
        self.messages.append({"role": "user", "content": parse_user_content(_sanitize(user_message))})

        # 重置工具循环检测
        self._tool_call_history = defaultdict(int)

        # debug_context 日志
        if self.debug_context:
            os.makedirs(os.path.dirname(_DEBUG_LOG), exist_ok=True)
            with open(_DEBUG_LOG, "w") as f:
                json.dump([], f)

        self._save_pending()

        rounds = [] if self.debug_context else None
        last_text_reply = None

        # ReAct 循环
        for iteration in range(max_iterations):
            self._maybe_trim_context()

            api_messages = to_openai_messages(
                [{"role": "system", "content": system}, *self.messages]
            )
            self._check_vision_capability(api_messages)
            kwargs = {
                "model": self.model,
                "messages": api_messages,
                "max_tokens": 4096,
            }

            if self.registry or self.memory_manager.providers:
                tools = []
                if self.registry:
                    tools.extend(self.registry.get_definitions(self.tool_names))
                mem_schemas = self.memory_manager.get_all_tool_schemas()
                existing_names = {s.get("function", s).get("name") for s in tools}
                for s in mem_schemas:
                    name = s.get("function", s).get("name") or s.get("name", "")
                    if not name or name in existing_names:
                        continue
                    if "type" not in s:
                        s = {"type": "function", "function": s}
                    tools.append(s)
                    existing_names.add(name)
                if tools:
                    kwargs["tools"] = tools

            # LLM 调用（统一入口，内部处理 retry/streaming）
            msg = self._call_llm_streaming(kwargs) if self.stream else self._call_llm(kwargs)

            if self.debug_context:
                rounds.append({"request": kwargs, "response": {"content": msg.content, "reasoning_content": getattr(msg, "reasoning_content", None), "tool_calls": [{"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in (msg.tool_calls or [])]}})
                with open(_DEBUG_LOG, "w") as f:
                    json.dump(rounds, f, ensure_ascii=False, indent=2)

            if msg.tool_calls:
                self.messages.append(self._build_assistant_msg(msg))
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    args_str = json.dumps(args, sort_keys=True) if args else "{}"

                    # 死循环检测
                    if self._detect_tool_loop(tc.function.name, args_str):
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"错误：工具 {tc.function.name} 已被连续调用 {_MAX_TOOL_LOOP} 次，疑似死循环。请换一种方式解决问题。",
                        })
                        self._save_pending()
                        # 继续循环让 LLM 有机会看到错误信息并调整策略
                        continue

                    if self.memory_manager.has_tool(tc.function.name):
                        t0 = time.time()
                        result = self.memory_manager.handle_tool_call(tc.function.name, args)
                        elapsed = int((time.time() - t0) * 1000)
                        logger.info("tool=%s source=memory_manager duration_ms=%d", tc.function.name, elapsed)
                    else:
                        t0 = time.time()
                        result = self.registry.dispatch(tc.function.name, args)
                        elapsed = int((time.time() - t0) * 1000)
                        logger.info("tool=%s source=registry duration_ms=%d", tc.function.name, elapsed)
                    log_event("tool_call", {
                        "tool": tc.function.name,
                        "args_truncated": args_str[:200],
                        "session_id": self.session_id,
                    })
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                    # 截图结果 → 注入 ImageBlock（后续迭代 LLM 可见）
                    self._maybe_inject_image(result)
                self._save_pending()
            else:
                content = msg.content or ""
                self.messages.append(self._build_assistant_msg(msg))
                self._save_pending()
                last_text_reply = content
                if content:
                    if self.stream:
                        return ""  # 已由 _call_llm_streaming 实时输出
                    return content
                return ""

        # 达到最大迭代次数
        self._save_pending()
        if last_text_reply:
            return f"{last_text_reply}\n\n---\n⚠ 已达到最大迭代次数 ({max_iterations})，如有需要请简化请求。"
        return f"已达到最大迭代次数 ({max_iterations})，对话可能不完整。如有需要请简化请求。"

    def _save_pending(self):
        """将尚未持久化的消息写入 session 数据库。"""
        if not self.session_db or not self.session_id:
            return
        pending = self.messages[self._saved_count:]
        if not pending:
            return
        self.session_db.save_messages(self.session_id, pending)
        self._saved_count = len(self.messages)

    def _maybe_trim_context(self):
        """消息超限时，先压缩 tool 结果，再从中间删除完整 assistant+tool 组。

        保护首尾，不破坏 assistant/tool 配对。
        """
        total = self._total_chars()
        if total <= self.max_context_chars:
            return

        chars_before = total

        # Phase 1: 压缩 tool 结果内容（非破坏性，保留结构完整）
        TOOL_MAX_LEN = 2000
        for m in self.messages:
            if m.get("role") == "tool" and len(m.get("content", "")) > TOOL_MAX_LEN:
                m["content"] = m["content"][:TOOL_MAX_LEN] + "\n...(truncated)"

        if self._total_chars() <= self.max_context_chars:
            logger.info("context_trim phase=1 before_chars=%d after_chars=%d",
                         chars_before, self._total_chars())
            return

        # Phase 2: 从中间逐组删除（assistant+tool 为原子单位）
        groups_removed = 0
        while self._total_chars() > self.max_context_chars:
            if len(self.messages) <= 3:
                break

            # 构建原子组 —— assistant(with tool_calls) + 紧随 tool 消息为整体
            groups: list[list[int]] = []
            i = 0
            while i < len(self.messages):
                if self.messages[i].get("role") == "assistant" and self.messages[i].get("tool_calls"):
                    group = [i]
                    i += 1
                    while i < len(self.messages) and self.messages[i].get("role") == "tool":
                        group.append(i)
                        i += 1
                    groups.append(group)
                else:
                    groups.append([i])
                    i += 1

            if len(groups) <= 3:
                break

            # 删除第 1 组之后、最后 2 组之前的最旧中间组
            target = groups[1]
            groups_removed += 1
            for pos in sorted(target, reverse=True):
                self.messages.pop(pos)

        if groups_removed:
            logger.warning("context_trim phase=2 before_chars=%d after_chars=%d groups_removed=%d",
                            chars_before, self._total_chars(), groups_removed)
            log_event("context_trim", {
                "phase": 2,
                "before_chars": chars_before,
                "after_chars": self._total_chars(),
                "groups_removed": groups_removed,
            })

    @staticmethod
    def _content_len(content: str | list) -> int:
        """计算 content 的字符数（兼容 ContentBlock list）。"""
        if isinstance(content, str):
            return len(content)
        if isinstance(content, list):
            total = 0
            for block in content:
                if isinstance(block, dict):
                    total += len(block.get("text", block.get("image_url", {}).get("url", "")))
                elif hasattr(block, "text"):
                    total += len(block.text)
            return total
        return 0

    def _total_chars(self) -> int:
        return sum(self._content_len(m.get("content") or "") for m in self.messages)
