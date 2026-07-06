"""AIAgent — ReAct 循环"""

import datetime
import json
import logging
import os
import re
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Callable

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
from agent.sub_agent import SubAgentManager

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
        # 子 Agent 目标（不为空时表示当前 Agent 是子 Agent，使用最小 prompt）
        self.goal: str = ""
        # registry / tool_names / memory 由外部注入，后续阶段改为构造参数注入
        self.registry: ToolRegistry | None = None
        self.tool_names: set[str] = set()
        # ── 工具分组：core 组直接加载，其他组延迟（LLM 通过 tool_request 激活）──
        self._deferred_tool_names: set[str] = set()
        # 当前请求的路由决策（由 _pre_route 设置）
        self._routing: dict = {}
        # 插件/MCP 注册的额外工具名
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
        # 冷冻 system prompt 缓存 —— 首次 run_conversation 时构建，全程复用
        self._frozen_base: str | None = None
        # 对话轮次计数器（跨 run_conversation 调用递增）
        self.turn_count: int = 0
        # 连续失败检测（同一工具连续报错 ≥3 次时强制停止）
        self._consecutive_failures: int = 0

        # ── 中断管理 ──
        # 使用 threading.Event（而非 bool），为未来多线程场景预留
        self._interrupt_requested = threading.Event()
        # ── 子 Agent 管理 ──
        self._sub_agent_manager = SubAgentManager()

        # ── 安全引擎 + 端侧快道（由 boot.py 注入 GuardEngine + FastLLM） ──
        self._guard_engine: Any = None  # safety.guard.GuardEngine
        self._fast_llm: Any = None  # endpoint.fast_llm.FastLLM

    def interrupt(self):
        """请求中断当前对话。线程安全（可在信号处理器中调用）。"""
        self._interrupt_requested.set()

    def _is_interrupted(self) -> bool:
        """检查是否收到中断请求。"""
        return self._interrupt_requested.is_set()

    def clear_interrupt(self):
        """清除中断请求。"""
        self._interrupt_requested.clear()

    # ── 降级状态重置 ──

    def _reset_fallback_session(self, is_top_level: bool = False):
        """重置 FallbackGateway 的会话级降级状态。

        只在顶层用户对话开始时重置（is_top_level=True），
        子 Agent 不重置，避免重复重试已失败的主模型。
        """
        if not is_top_level:
            return
        gw = self.gateway
        # 穿透 UsageRecorder 装饰器
        if hasattr(gw, '_inner'):
            gw = gw._inner
        if hasattr(gw, 'reset_session'):
            try:
                gw.reset_session()
            except Exception:
                pass

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

    # ── 冷冻缓存 ──

    def _ensure_cache(self):
        """构建冷冻 system prompt 缓存（仅首次执行）。

        子 Agent（self.goal 不为空）使用最小 prompt，
        只包含目标和基本行为约束，不继承主 Agent 的完整 identity。
        """
        if self._frozen_base is not None:
            return
        if self.goal:
            self._frozen_base = self.prompt_builder.build_minimal(goal=self.goal)
            return
        snapshot = self.memory_manager.snapshot()
        self._frozen_base = self.prompt_builder.build_frozen(
            memory_snapshot=snapshot,
            context_files=self.context_files,
            skills_index=self.skills_index,
        )

    # ── 工具解析（Deferred / Permanent / Core） ──

    def _resolve_tool_names(self):
        """按工具分组重新计算 tool_names 和 _deferred_tool_names。

        core 组工具永远加载，其他组工具默认延迟。
        """
        if not self.registry:
            return

        from agent.tool_groups import DEFERRED_GROUPS

        # 重新计算 deferred 列表：清理已不存在或已被激活的工具
        all_registered = self.registry.tool_names
        self._deferred_tool_names &= all_registered

        registered = self.registry._entries

        core_tools: set[str] = set()
        deferred_tools: set[str] = set()

        for name, entry in registered.items():
            if entry.group == "core":
                core_tools.add(name)
            elif entry.group in DEFERRED_GROUPS:
                deferred_tools.add(name)

        # core 工具 + 已激活的 deferred 工具 + MCP/插件工具
        activated = self._deferred_tool_names
        self.tool_names = (core_tools | activated | self._extra_tool_names) & all_registered

        # 未激活的 deferred 工具列表（供 prompt 显示）
        self._deferred_tool_names = (deferred_tools - self.tool_names) & all_registered

    def activate_deferred_tool(self, name: str) -> bool:
        """将延迟工具激活到当前会话。返回是否成功。"""
        if not self.registry:
            return False
        entry = self.registry._entries.get(name)
        if entry is None:
            return False
        if entry.group == "core":
            return True  # core 工具已经在 tool_names 里
        self._deferred_tool_names.discard(name)
        self.tool_names.add(name)
        logger.info("deferred_tool_activated name=%s", name)
        return True

    def _build_deferred_block(self) -> str:
        if not self._deferred_tool_names:
            return ""
        from agent.tool_groups import TOOL_GROUPS
        from tool.registry import registry
        lines = ["<available-deferred-tools>"]
        lines.append("以下工具已注册但未激活，可用 tool_describe 查看详情、tool_request 激活：")
        lines.append("")
        by_group: dict[str, list[str]] = {}
        for name in sorted(self._deferred_tool_names):
            entry = registry._entries.get(name)
            if entry is None:
                continue
            g = entry.group
            if g not in by_group:
                by_group[g] = []
            fn = entry.schema.get("function", entry.schema)
            desc = fn.get("description", "")[:30]
            by_group[g].append(f"  {name}: {desc}")
        for g in sorted(by_group):
            group_info = TOOL_GROUPS.get(g, {})
            label = group_info.get("name", g)
            lines.append(f"[{label}]")
            lines.extend(by_group[g])
            lines.append("")
        lines.append("</available-deferred-tools>")
        return "\n".join(lines)

    # ── 子 Agent 管理 ──

    def fork_sub_agent(
        self,
        agent_name: str,
        task: str,
        *,
        model: str | None = None,
        tools: list[str] | None = None,
        max_iterations: int = 10,
        context: str = "",
        _agent_callback: Callable | None = None,
    ) -> str:
        if _agent_callback is None:
            _agent_callback = getattr(self, '_agent_callback', None)
        """创建并同步执行一个子 Agent，返回 record id。

        子 Agent 共享父 Agent 的 gateway / registry / memory_manager，
        拥有独立的消息历史。执行完毕后结果存入 _sub_agent_manager，
        父 Agent 可通过 get_sub_agent_result(record_id) 追溯详情。

        Args:
            agent_name: 角色名（如 "researcher"）或 "(inline)"
            task: 子任务描述
            model: 子 Agent 使用的模型（默认继承父模型）
            tools: 子 Agent 可用工具集（默认继承父工具）
            max_iterations: 最大 ReAct 迭代次数，默认 10
            context: 附加上下文，拼在 task 前

        Returns:
            子 Agent 记录的 id（用于后续 get_sub_agent_result 查询）
        """
        record_id = self._sub_agent_manager.create_with_ttl(agent_name, task, ttl=300)
        self._sub_agent_manager.update(record_id, status="running")  # str → AgentStatus 自动转换

        # TUI 回调：子 Agent 开始
        if _agent_callback:
            _agent_callback(agent_name, task, None, record_id)

        try:
            from tool.builtins.agent_tools import build_sub_agent

            sub, final_task, max_iterations = build_sub_agent(
                task=task,
                parent=self,
                model=model or self.model,
                tools=tools,
                max_iterations=max_iterations,
                context=context,
                agent_name=agent_name,
                session_db=self.session_db,
                session_id=self.session_id or "",
            )

            output = sub.run_conversation(final_task, max_iterations=max_iterations)
            self._sub_agent_manager.capture_result(record_id, sub.messages, output)
            # TUI 回调：子 Agent 完成
            if _agent_callback:
                record = self._sub_agent_manager.get(record_id)
                summary = (output or "")[:200]
                _agent_callback(agent_name, task, summary, record_id)
            logger.info(
                "fork_sub_agent done id=%s name=%s iter=%d tools=%d",
                record_id, agent_name,
                self._sub_agent_manager.get(record_id).iterations,
                len(self._sub_agent_manager.get(record_id).tool_calls),
            )
        except Exception as e:
            logger.exception("fork_sub_agent failed name=%s task=%r", agent_name, task[:80])
            self._sub_agent_manager.capture_result(record_id, [], "", error=str(e))

        return record_id

    def list_sub_agents(self, agent_name: str | None = None) -> list[dict]:
        """列出子 Agent 执行记录（不含消息内容）。"""
        return self._sub_agent_manager.list(agent_name)

    def get_sub_agent_result(self, record_id: str) -> dict | None:
        """获取子 Agent 完整结果（含消息链、工具调用、token）。"""
        record = self._sub_agent_manager.get(record_id)
        if record is None:
            return None
        from dataclasses import asdict
        result = asdict(record)
        # 枚举转字符串值，确保 JSON 序列化正确
        if "status" in result and hasattr(result["status"], "value"):
            result["status"] = result["status"].value
        return result

    # ── 安全拦截 ──

    def _pre_route(self, user_message: str) -> str | None:
        """安全拦截。

        Returns:
            str   — 驳回消息
            None  — 继续
        """
        if self._guard_engine is not None:
            decision = self._guard_engine.evaluate(user_message)
            if decision.is_block():
                logger.info("ROUTE msg=%s guard=block rule=%s reason=%s",
                             user_message[:30], decision.matched_rule, decision.reason)
                try:
                    from gateway.metrics import guard_blocks_total
                    guard_blocks_total.labels(rule=decision.matched_rule or "unknown").inc()
                except Exception:
                    pass
                return f"⛔ 操作已被拦截\n\n原因：{decision.reason}"
        return None

    def _classify_intent(self, user_message: str) -> dict:
        """调用小模型分类，返回路由决策。"""
        if self._fast_llm is not None and self._fast_llm.is_available():
            result = self._fast_llm.classify(user_message)
        else:
            if self._fast_llm is not None:
                import sys
                print("\n  ⚡ 端侧模型（Ollama）不可用，小模型直答已跳过", file=sys.stderr)
            result = {"intent": "other", "predicted_tools": [], "confidence": "low"}

        # 检查黑名单 + 获取决策原因
        from agent.intent_config import classify_route
        channel, reason = classify_route(result["intent"], result.get("predicted_tools", []))

        # 小模型通道 → 设置模型覆盖 + 模型可见范围
        if channel == "small":
            if self._fast_llm is not None:
                result["_model_override"] = self._fast_llm.MODEL
            result["_model_scope"] = "small"
        else:
            result["_model_scope"] = "large"

        result["channel"] = channel
        result["route_reason"] = reason

        # 路由决策追踪日志
        scores = [c.get("score") for c in result.get("candidates", [])]
        logger.info("ROUTE msg=%s guard=pass classify=intent:%s(%s)/tools:%s "
                     "channel=%s reason=%s",
                     user_message[:30], result["intent"],
                     scores[0] if scores else "?",
                     result.get("predicted_tools", []),
                     channel, reason)

        # 路由指标
        try:
            from gateway.metrics import routing_decisions_total
            routing_decisions_total.labels(channel=channel, intent=result["intent"]).inc()
        except Exception:
            pass

        return result

    def _route_small_direct(self, user_message: str) -> str | None:
        """小模型直答（不走 ReAct，极速通道）。"""
        if self._fast_llm is None or not self._fast_llm.is_available():
            return None

        import json, urllib.request
        payload = json.dumps({
            "model": self._fast_llm.MODEL,
            "messages": [{"role": "user", "content": user_message}],
            "stream": False,
            "options": {"num_predict": 128},
        }).encode()

        try:
            req = urllib.request.Request(
                f"{self._fast_llm.OLLAMA_BASE}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=15)
            body = json.loads(resp.read())
            reply = body.get("message", {}).get("content", "").strip()
            if reply:
                reply += " \033[38;2;100;100;120m⚡\033[0m"
                self.messages.append({"role": "user", "content": user_message})
                self.messages.append({"role": "assistant", "content": reply})
                logger.info("small_direct_reply reply=%s", reply[:60])
                return reply
        except Exception as exc:
            import sys
            print(f"\n  ⚡ 小模型直答失败（{exc}），切到大模型", file=sys.stderr)
            logger.warning("small_direct_failed: %s", exc)
            return None
        return None

    def _prepare_conversation(self, user_message: str) -> str:
        """构建 system prompt + 初始化本轮对话环境。返回 system prompt 字符串。"""
        self._ensure_cache()

        is_small = self._routing.get("channel") == "small"

        if is_small:
            # 小模型通道：极简 prompt，只有一句话 + 用户消息 + 一个工具
            system = "你是 chps。用中文回答。"
            self.messages.append({"role": "user", "content": parse_user_content(_sanitize(user_message))})
            self.memory_manager.initialize_all(session_id=self.session_id)
            self._tool_call_history = defaultdict(int)
            self._consecutive_failures = 0
            self._save_pending()
            return system
        else:
            # 大模型通道：完整 prompt
            prefetch = self.memory_manager.prefetch_all(user_message)
            from knowledge.manager import KnowledgeManager
            if not hasattr(self, '_knowledge_manager') or self._knowledge_manager is None:
                self._knowledge_manager = KnowledgeManager()
            knowledge_entries = self._knowledge_manager.match(user_message)
            knowledge = self._knowledge_manager.format_knowledge(knowledge_entries)
            from tool.toolsets import build_availability_table
            avail = build_availability_table()

            dynamic = self.prompt_builder.build_dynamic(
                prefetch=prefetch,
                timestamp=str(datetime.date.today()),
                toolset_availability=avail,
                knowledge=knowledge,
            )

        system = (self._frozen_base or "") + "\n\n" + dynamic
        if not is_small:
            deferred_block = self._build_deferred_block()
            if deferred_block:
                system += "\n\n" + deferred_block
        # 复杂任务提示：检测到多步骤/多文件/多角色任务时，提醒 LLM 考虑 orchestrate
        if not is_small and self._routing.get("intent") in ("complex", "delegate", "simple_coding"):
            system += (
                "\n\n<complex-task-hint>\n"
                "当前任务看起来需要多个步骤。如果涉及以下情况，请使用 orchestrate 工具：\n"
                "  - 需要同时查资料和写代码\n"
                "  - 需要操作多个独立文件或模块\n"
                "  - 可以用多个角色并行工作（研究员查资料 + 程序员实现）\n"
                "  - 需要先调研再做决策\n"
                "</complex-task-hint>"
            )
        self.messages.append({"role": "user", "content": parse_user_content(_sanitize(user_message))})

        self.memory_manager.initialize_all(session_id=self.session_id)
        self._tool_call_history = defaultdict(int)
        self._consecutive_failures = 0

        if self.debug_context:
            os.makedirs(os.path.dirname(_DEBUG_LOG), exist_ok=True)
            with open(_DEBUG_LOG, "w") as f:
                json.dump([], f)

        os.environ["CHIPS_SESSION_ID"] = self.session_id
        self._save_pending()
        return system

    # ── Trace 收集（供小模型学习用） ──

    def _collect_trace(self, user_message: str, final_reply: str):
        """从本轮 messages 中提取 tool_call 序列，写入 history/ 供后续分析。"""
        tool_calls = []
        for msg in self.messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tool_calls.append({
                        "name": tc["function"]["name"],
                        "args": tc["function"]["arguments"],
                    })
            elif msg.get("role") == "tool" and tool_calls and "result" not in tool_calls[-1]:
                tool_calls[-1]["result"] = (msg.get("content", "") or "")[:300]

        if len(tool_calls) < 2:
            return None

        trace = {
            "timestamp": datetime.datetime.now().isoformat(),
            "user_message": user_message[:500],
            "final_reply": (final_reply or "")[:500],
            "tool_calls": tool_calls,
            "total_steps": len(tool_calls),
        }
        history_dir = os.path.expanduser("~/.chips/knowledge/history")
        os.makedirs(history_dir, exist_ok=True)
        fname = f"{datetime.date.today()}-{int(time.time())}.json"
        with open(os.path.join(history_dir, fname), "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2)
        return trace

    def _analyze_and_learn(self, user_message: str, final_reply: str):
        """小模型分析本轮 trace → staging（串联 T2 + T3）。"""
        trace = self._collect_trace(user_message, final_reply)
        if trace is None:
            return
        if self._fast_llm is None or not self._fast_llm.is_available():
            return

        from knowledge.manager import KnowledgeManager
        if not hasattr(self, '_knowledge_manager') or self._knowledge_manager is None:
            self._knowledge_manager = KnowledgeManager()

        analysis = self._fast_llm.analyze_trace(trace)
        if not analysis.get("optimal"):
            return

        staging_id = self._knowledge_manager.save_to_staging(analysis)
        if staging_id:
            logger.info("knowledge_learned id=%s task=%s optimal=%s",
                         staging_id, analysis.get("task"), analysis.get("optimal", "")[:40])

    # ── 主循环 ──

    def run_conversation(self, user_message: str, max_iterations: int = 20, *,
                         chunk_callback=None, tool_callback=None, agent_callback=None) -> str:
        self._agent_callback = agent_callback
        self.turn_count += 1
        # 新一轮对话，重置模型降级状态（让主用模型有机会重新尝试）
        self._reset_fallback_session(agent_callback is not None)
        # 活跃会话数 +1
        try:
            from gateway.metrics import session_active
            session_active.inc()
        except Exception:
            pass
        # 更新日志轮次，后续所有 log record 将携带 turn_number
        from agent.logger import set_turn_number
        set_turn_number(self.turn_count)
        self.clear_interrupt()  # 清除上一轮可能残留的中断信号

        # 阶段一：安全拦截
        reply = self._pre_route(user_message)
        if reply is not None:
            return reply

        # 阶段二：意图分类 + 工具预激活（在 _prepare_conversation 之前，
        # 让小模型通道能拿到 _routing 做精简 prompt）
        self._routing = self._classify_intent(user_message)
        for tool in self._routing.get("predicted_tools", []):
            self.activate_deferred_tool(tool)

        # 阶段三：小模型直答（不走 ReAct，极速通道）
        if self._routing.get("channel") == "small":
            reply = self._route_small_direct(user_message)
            if reply is not None:
                return reply

        # 阶段四：对话准备（system prompt + memory 预热）
        system = self._prepare_conversation(user_message)

        # 阶段五：ReAct 循环
        rounds = [] if self.debug_context else None
        last_text_reply = None
        try:
            for iteration in range(max_iterations):
                # 中断检查 ①：每次迭代开始
                if self._is_interrupted():
                    logger.info("interrupt_requested iteration=%d", iteration)
                    break

                # 智能压缩（摘要保留信息，有 context engine 时优先）
                if self.context_engine and self.context_engine.should_compress():
                    self.messages = self.context_engine.compress(self.messages)
                # 安全网（超 100K 字符时的硬裁剪）
                self._maybe_trim_context()

                api_messages = to_openai_messages(
                    [{"role": "system", "content": system}, *self.messages]
                )
                self._check_vision_capability(api_messages)
                model = self._routing.get("_model_override") or self.model
                gw = self._ollama_gateway if self._routing.get("_model_override") else self.gateway
                kwargs = {
                    "model": model,
                    "messages": api_messages,
                    "max_tokens": 4096,
                }

                self._resolve_tool_names()

                if self.registry or self.memory_manager.providers:
                    tools = []
                    if self.registry:
                        scope = self._routing.get("_model_scope")
                        defs = self.registry.get_definitions(self.tool_names, model_scope=scope)
                        # 小模型通道：根据 intent 只保留允许的工具
                        if scope == "small":
                            intent = self._routing.get("intent", "other")
                            from agent.intent_config import get_tools_for_intent
                            allowed = get_tools_for_intent(intent)
                            if isinstance(allowed, list):
                                allowed_set = set(allowed)
                                defs = [d for d in defs
                                        if d.get("function", d).get("name") in allowed_set]
                        tools.extend(defs)
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

                # 中断检查 ②：在调用 LLM 之前，避免浪费 tokens
                if self._is_interrupted():
                    logger.info("interrupt_requested before_llm iteration=%d", iteration)
                    break

                # LLM 调用前钩子（给 observability 插件用）
                if self.plugin_manager:
                    kwargs = self.plugin_manager.dispatch_llm_call_pre(
                        api_messages, self.model, kwargs,
                    )

                # LLM 调用（通过 gateway，内部处理 retry/streaming）
                _llm_t0 = time.time()
                if self.stream:
                    _on_chunk = chunk_callback or (lambda c: print(c, end="", flush=True))
                    _buf: list[str] = []  # 仅用于事后安全检查，不影响实时输出

                    def _collecting_chunk(text: str):
                        _buf.append(text)
                        _on_chunk(text)  # 立即输出，不缓冲

                    result = gw.chat_stream(
                        messages=api_messages, model=model,
                        max_tokens=4096, tools=tools if tools else None,
                        on_chunk=_collecting_chunk,
                    )
                    # 纯文本流式输出结束后换行
                    if not result.tool_calls:
                        print()
                else:
                    result = gw.chat(
                        messages=api_messages, model=model,
                        max_tokens=4096, tools=tools if tools else None,
                    )
                _llm_duration = int((time.time() - _llm_t0) * 1000)
                # LLM 调用后钩子（给 observability 插件用）
                if self.plugin_manager:
                    self.plugin_manager.dispatch_llm_call_post(
                        api_messages, self.model, result, _llm_duration,
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

                        # TUI 工具回调（调用前）
                        if tool_callback:
                            tool_callback(name, args, None)

                        # 工具执行判断， memory虽然是register发现，但是执行时被截断，只有tool被调用dispatch
                        t0 = 0.0
                        try:
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
                                # Deferred Tools: 如果 LLM 调用了 toolset 名而非工具名，给提示
                                if tool_result.startswith('{"error": "unknown tool:'):
                                    from tool.toolsets import get_toolset
                                    if get_toolset(name):
                                        tool_result = json.dumps({
                                            "error": f"'{name}' 是工具集名，不是工具名。请先通过 toolset enable {name} 激活工具集，然后使用具体的工具名（如 toolset list 查看）"
                                        })
                        except Exception as e:
                            elapsed = int((time.time() - t0) * 1000)
                            tool_result = json.dumps({"error": f"工具执行异常: {e}"}, ensure_ascii=False)
                            logger.exception("tool_exception tool=%s duration_ms=%d", name, elapsed)
                        # 持久化工具调用记录
                        tool_status = "error" if tool_result.startswith('{"error"') else "success"
                        if self.session_db:
                            try:
                                self.session_db.insert_tool_call(
                                    session_id=self.session_id,
                                    turn_number=self.turn_count,
                                    tool_name=name,
                                    status=tool_status,
                                    duration_ms=elapsed,
                                )
                            except Exception:
                                pass
                        # 同步更新 Prometheus 工具指标
                        try:
                            from gateway.metrics import tool_calls_total, tool_duration_seconds
                            tool_calls_total.labels(tool_name=name, status=tool_status).inc()
                            tool_duration_seconds.labels(tool_name=name).observe(elapsed / 1000.0)
                        except Exception:
                            pass
                        # TUI 工具回调（调用后）
                        if tool_callback:
                            tool_callback(name, args, tool_result)
                        # 插件钩子：工具调用后
                        if self.plugin_manager:
                            tool_result = self.plugin_manager.dispatch_tool_call_post(name, tool_result)
                        log_event("tool_call", {
                            "tool": name,
                            "args_truncated": args_str[:200],
                            "session_id": self.session_id,
                        })
                        # 连续失败检测
                        if tool_result.startswith('{"error"') or tool_result.startswith("错误："):
                            self._consecutive_failures += 1
                            if self._consecutive_failures >= 3:
                                tool_result = (
                                    f"错误：工具 {name} 已连续失败 {self._consecutive_failures} 次，"
                                    "说明当前方法行不通。请停止重试，换完全不同的策略，"
                                    "或直接向用户说明失败原因。"
                                )
                        else:
                            self._consecutive_failures = 0
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": tool_result,
                        })
                        # 截图结果 → 注入 ImageBlock（后续迭代 LLM 可见）
                        self._maybe_inject_image(tool_result)
                    self._save_pending()
                    # 中断检查 ③：工具执行批后，在此轮结束前检查
                    if self._is_interrupted():
                        logger.info("interrupt_requested after_tools iteration=%d", iteration)
                        break
                else:
                    content = result.content or ""
                    if self.plugin_manager:
                        content = self.plugin_manager.dispatch_response(content)
                    # 输出护栏（事后检查）：不影响实时流式输出
                    from agent.prompt import check_output_safety
                    safety_hit = check_output_safety(content)
                    if safety_hit:
                        logger.warning("output_safety_blocked pattern=%s content_truncated=%s", safety_hit, content[:200])
                        # 非流式模式：替换最终返回内容和消息历史
                        # 流式模式：内容已实时输出，仅记录日志，不篡改历史
                        if not self.stream:
                            result.content = f"⚠ 回复已被过滤（命中输出护栏：[{safety_hit}]）"
                            content = result.content
                    self.messages.append(self._build_assistant_msg(result))
                    self._save_pending()
                    last_text_reply = content
                    if content:
                        if self.stream:
                            return ""  # 已由 chat_stream 的 on_chunk 实时输出
                        return content
                    return ""

            # 中断 或 达到最大迭代次数
            self._save_pending()
            if self._is_interrupted():
                if last_text_reply:
                    return f"{last_text_reply}\n\n---\n⚠ 对话已被中断"
                return "⚠ 对话已被中断"
            if last_text_reply:
                return f"{last_text_reply}\n\n---\n⚠ 已达到最大迭代次数 ({max_iterations})，如有需要请简化请求。"
            return f"已达到最大迭代次数 ({max_iterations})，对话可能不完整。如有需要请简化请求。"
        finally:
            try:
                from gateway.metrics import session_active
                session_active.dec()
            except Exception:
                pass
            self.memory_manager.sync_all(user_message, last_text_reply or "", session_id=self.session_id)
            self.memory_manager.on_session_end(self.messages)
            self._collect_trace(user_message, last_text_reply)
            self._analyze_and_learn(user_message, last_text_reply)
            if self.plugin_manager:
                self.plugin_manager.dispatch_session_end(self.messages)

    def shutdown(self):
        """释放资源：关闭 MCP 连接、清理线程和所有记忆提供者。"""
        if hasattr(self, "mcp_manager") and self.mcp_manager:
            self.mcp_manager.stop_all()
        self._sub_agent_manager.stop_cleaner()
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
