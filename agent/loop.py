"""AIAgent — ReAct 循环"""

import json
import logging
import os
import re
import sys
import time
from collections import defaultdict

# 移除 surrogate 字符（如 DeepSeek reasoning_content 中可能出现的 \udce4），
# 防止后续请求序列化时 UnicodeEncodeError: surrogates not allowed
_SURROGATE_RE = re.compile('[\ud800-\udfff]')


def _sanitize(text: str) -> str:
    return _SURROGATE_RE.sub("", text)

logger = logging.getLogger("chips.agent.loop")

from agent.message import ImageBlock, TextBlock, image_file_to_data_uri, parse_user_content, to_openai_messages
from agent.prompt import PromptBuilder
from agent.context_engine import ContextEngine
from gateway.protocol import ModelGateway
from gateway.types import ChatResult
from memory.manager import MemoryManager
from plugins.manager import PluginManager
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
        model: str = "deepseek-chat",
        debug_context: bool = False,
        verbose: bool = False,
        stream: bool = False,
        max_retries: int = 3,
        gateway: ModelGateway | None = None,
    ):
        self.gateway = gateway
        self.model = model
        self.debug_context = debug_context
        self.verbose = verbose
        self.stream = stream
        self._max_retries = max_retries
        self.prompt_builder = PromptBuilder(verbose=verbose)
        # registry / tool_names / memory 由外部注入，后续阶段改为构造参数注入
        self.registry: ToolRegistry | None = None
        self.tool_names: set[str] = set()
        # 启用的 toolset 名列表（动态开关用），由 cli.py 注入
        self.enabled_toolsets: list[str] = []
        # 插件/MCP 注册的额外工具名（不受 toolset 开关影响）
        self._extra_tool_names: set[str] = set()
        self.memory_manager = MemoryManager()
        # 插件管理器，由 cli.py 在启动时初始化注入
        self.plugin_manager: PluginManager | None = None
        # 上下文压缩引擎，由 cli.py 在启动时注入
        self.context_engine: ContextEngine | None = None
        # 上下文文件列表，由 cli.py 在启动时搜索注入
        self.context_files: list[tuple[str, str, str]] = []
        # <available_skills> 索引，由 cli.py 在启动时注入
        self.skills_index: str = ""
        # 当前轮次的对话消息历史，tool_calls 结果也会追加进来
        self.messages: list[dict] = []
        # session 持久化，由 cli.py wiring 注入
        self.session_db: SessionDB | None = None
        self.session_id: str = ""
        self._saved_count: int = 0
        # 上下文压缩：消息总字符超限时裁剪历史
        self.max_context_chars: int = 100_000

    # ── 消息构建 ──

    def _build_assistant_msg(self, msg) -> dict:
        """将 ChatResult / assistant 消息转为可追加到 self.messages 的 dict。"""
        d = {"role": "assistant", "content": _sanitize(msg.content or "")}
        rc = getattr(msg, "reasoning_content", None)
        if rc:
            d["reasoning_content"] = _sanitize(rc)
        if msg.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.get("id", "") if isinstance(tc, dict) else tc.id,
                    "type": "function",
                    "function": {
                        "name": _sanitize(tc["function"]["name"] if isinstance(tc, dict) else tc.function.name),
                        "arguments": _sanitize(tc["function"]["arguments"] if isinstance(tc, dict) else tc.function.arguments),
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

    def run_conversation(self, user_message: str, max_iterations: int = 20, *, chunk_callback=None) -> str:
        # system prompt 每次重新构建
        mem_prompt = self.memory_manager.build_system_prompt()
        prefetch = self.memory_manager.prefetch_all(user_message)
        if prefetch:
            mem_prompt = (mem_prompt + "\n\n" + prefetch) if mem_prompt else prefetch
        from tool.toolsets import build_availability_table
        system = self.prompt_builder.build(
            memory_prompt=mem_prompt,
            context_files=self.context_files,
            skills_index=self.skills_index,
            toolset_availability=build_availability_table(),
        )
        self.messages.append({"role": "user", "content": parse_user_content(_sanitize(user_message))})

        # 初始化记忆提供者（建库、连接等）
        self.memory_manager.initialize_all(session_id=self.session_id)

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
        try:
            for iteration in range(max_iterations):
                # 智能压缩（摘要保留信息，有 context engine 时优先）
                if self.context_engine and self.context_engine.should_compress():
                    self.messages = self.context_engine.compress(self.messages)
                # 安全网（超 100K 字符时的硬裁剪）
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

                # 从 enabled_toolsets 重新解析 tool_names（支持运行时动态开关）
                if self.registry and self.enabled_toolsets:
                    from tool.toolsets import resolve_multiple_toolsets
                    base = resolve_multiple_toolsets(self.enabled_toolsets)
                    self.tool_names = (set(base) | self._extra_tool_names) & self.registry.tool_names

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

                # LLM 调用（通过 gateway，内部处理 retry/streaming）
                if self.stream:
                    _on_chunk = chunk_callback or (lambda c: print(c, end="", flush=True))
                    result = self.gateway.chat_stream(
                        messages=api_messages, model=self.model,
                        max_tokens=4096, tools=tools if tools else None,
                        on_chunk=_on_chunk,
                    )
                    # 纯文本流式输出结束后换行
                    if not result.tool_calls:
                        print()
                else:
                    result = self.gateway.chat(
                        messages=api_messages, model=self.model,
                        max_tokens=4096, tools=tools if tools else None,
                    )

                if self.debug_context:
                    rounds.append({"request": {"model": self.model, "messages": api_messages, "max_tokens": 4096, "tools": tools if tools else None}, "response": {"content": result.content, "reasoning_content": result.reasoning_content, "tool_calls": result.tool_calls}})
                    with open(_DEBUG_LOG, "w") as f:
                        json.dump(rounds, f, ensure_ascii=False, indent=2)

                # 通知上下文引擎用量（用于判断是否需要压缩）
                if self.context_engine and result.usage:
                    self.context_engine.update_from_response(result.usage)

                if result.tool_calls:
                    self.messages.append(self._build_assistant_msg(result))
                    for tc in result.tool_calls:
                        try:
                            args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError:
                            args = {}
                        args_str = json.dumps(args, sort_keys=True) if args else "{}"

                        # 死循环检测
                        if self._detect_tool_loop(tc["function"]["name"], args_str):
                            self.messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": f"错误：工具 {tc['function']['name']} 已被连续调用 {_MAX_TOOL_LOOP} 次，疑似死循环。请换一种方式解决问题。",
                            })
                            self._save_pending()
                            # 继续循环让 LLM 有机会看到错误信息并调整策略
                            continue

                        name = tc["function"]["name"]
                        # 插件钩子：工具调用前
                        if self.plugin_manager:
                            args = self.plugin_manager.dispatch_tool_call_pre(name, args)

                        # 工具执行判断， memory虽然是register发现，但是执行时被截断，只有tool被调用dispatch
                        if self.memory_manager.has_tool(name):
                            t0 = time.time()
                            tool_result = self.memory_manager.handle_tool_call(name, args)
                            elapsed = int((time.time() - t0) * 1000)
                            logger.info("tool=%s source=memory_manager duration_ms=%d", name, elapsed)
                        else:
                            t0 = time.time()
                            tool_result = self.registry.dispatch(name, args)
                            elapsed = int((time.time() - t0) * 1000)
                            logger.info("tool=%s source=registry duration_ms=%d", name, elapsed)
                        # 插件钩子：工具调用后
                        if self.plugin_manager:
                            tool_result = self.plugin_manager.dispatch_tool_call_post(name, tool_result)
                        log_event("tool_call", {
                            "tool": name,
                            "args_truncated": args_str[:200],
                            "session_id": self.session_id,
                        })
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": tool_result,
                        })
                        # 截图结果 → 注入 ImageBlock（后续迭代 LLM 可见）
                        self._maybe_inject_image(tool_result)
                    self._save_pending()
                else:
                    content = result.content or ""
                    if self.plugin_manager:
                        content = self.plugin_manager.dispatch_response(content)
                    self.messages.append(self._build_assistant_msg(result))
                    self._save_pending()
                    last_text_reply = content
                    if content:
                        if self.stream:
                            return ""  # 已由 chat_stream 的 on_chunk 实时输出
                        return content
                    return ""

            # 达到最大迭代次数
            self._save_pending()
            if last_text_reply:
                return f"{last_text_reply}\n\n---\n⚠ 已达到最大迭代次数 ({max_iterations})，如有需要请简化请求。"
            return f"已达到最大迭代次数 ({max_iterations})，对话可能不完整。如有需要请简化请求。"
        finally:
            self.memory_manager.sync_all(user_message, last_text_reply or "", session_id=self.session_id)
            self.memory_manager.on_session_end(self.messages)
            if self.plugin_manager:
                self.plugin_manager.dispatch_session_end(self.messages)

    def shutdown(self):
        """释放资源：关闭 MCP 连接和所有记忆提供者。"""
        if hasattr(self, "mcp_manager") and self.mcp_manager:
            self.mcp_manager.stop_all()
        self.memory_manager.shutdown_all()

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
