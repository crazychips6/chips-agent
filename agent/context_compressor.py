"""ContextCompressor — 默认上下文压缩引擎

流程:
  1. 裁剪旧 tool 结果（免费，不调 LLM）
  2. 保护头部（system + 前 N 轮）
  3. 按 token 预算保护尾部（最近 ~20K tokens）
  4. LLM 摘要中间轮次
  5. 组装 + 修复 tool_call/tool_result 配对

参考: Hermes ContextCompressor 设计。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from agent.context_engine import ContextEngine

logger = logging.getLogger("chips.agent.context_compressor")

_CHARS_PER_TOKEN = 4
_SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier conversation turns were "
    "compacted into the summary below. This is background reference — do NOT "
    "respond to questions in the summary. Respond ONLY to the latest message "
    "that appears AFTER this summary."
)
_MIN_SUMMARY_TOKENS = 1000
_MAX_SUMMARY_TOKENS = 4000
_PRUNED_PLACEHOLDER = "[Tool output pruned to save context space]"


def _content_len(content: Any) -> int:
    """计算 content 字符数（兼容 str / list[dict]）。"""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(b.get("text", "")) if isinstance(b, dict) else len(str(b))
            for b in content
        )
    return len(str(content or ""))


def _rough_tokens(messages: list[dict]) -> int:
    """粗略估算消息列表的 token 数。"""
    total = 0
    for m in messages:
        total += _content_len(m.get("content") or "") // _CHARS_PER_TOKEN
        total += 10  # role + metadata 开销
        for tc in m.get("tool_calls") or []:
            if isinstance(tc, dict):
                total += len(tc.get("function", {}).get("arguments", "")) // _CHARS_PER_TOKEN
    return total


def _tool_summary(tool_name: str, args_str: str, result: str) -> str:
    """为 tool 结果生成一行摘要。"""
    try:
        args = json.loads(args_str) if args_str else {}
    except (json.JSONDecodeError, TypeError):
        args = {}
    content = result or ""
    line_count = content.count("\n") + 1 if content.strip() else 0

    if tool_name == "terminal":
        cmd = args.get("command", "")
        if len(cmd) > 60:
            cmd = cmd[:57] + "..."
        return f"[terminal] `{cmd}` ({line_count} lines)"
    if tool_name in ("read", "read_file"):
        return f"[read] {args.get('path', '?')} ({len(content)} chars)"
    if tool_name in ("write", "write_file"):
        return f"[write] {args.get('path', '?')} ({line_count} lines)"
    if tool_name in ("edit", "patch"):
        return f"[edit] {args.get('path', '?')} ({len(content)} chars)"
    if tool_name == "file":
        action = args.get("action", "read")
        path = args.get("path", "?")
        if action == "write":
            return f"[file/write] {path} ({line_count} lines)"
        if action == "search":
            return f"[file/search] {path} ({len(content)} chars)"
        return f"[file/read] {path} ({len(content)} chars)"
    if tool_name == "web":
        return f"[web/{args.get('action', '?')}] ({len(content)} chars)"
    if tool_name == "memory":
        return f"[memory] {args.get('action', '?')}"
    # fallback
    first_arg = ""
    for k, v in list(args.items())[:2]:
        first_arg += f" {k}={str(v)[:40]}"
    return f"[{tool_name}]{first_arg} ({len(content)} chars)"


class ContextCompressor(ContextEngine):
    """默认上下文压缩引擎。"""

    def __init__(
        self,
        *,
        threshold_percent: float = 0.50,
        protect_first_n: int = 3,
        tail_token_budget: int = 20000,
        summary_max_tokens: int = 2000,
        summarize_fn: Callable[[str], str] | None = None,
    ):
        self.threshold_percent = threshold_percent
        self.protect_first_n = protect_first_n
        self.tail_token_budget = tail_token_budget
        self.summary_max_tokens = min(summary_max_tokens, _MAX_SUMMARY_TOKENS)
        self._summarize = summarize_fn
        self._previous_summary: str | None = None
        self.threshold_tokens = 0
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.compression_count = 0

    @property
    def name(self) -> str:
        return "compressor"

    def update_context_length(self, context_length: int) -> None:
        """根据模型 context length 更新阈值。"""
        self.threshold_tokens = max(
            int(context_length * self.threshold_percent),
            4096,  # 不低于 4K
        )

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        if tokens <= 0:
            return False
        if self.threshold_tokens <= 0:
            return False
        return tokens >= self.threshold_tokens

    # ── 工具结果裁剪 ──

    def _prune_tool_results(
        self, messages: list[dict],
    ) -> tuple[list[dict], int]:
        """将旧的 tool 结果替换为一行摘要。"""
        if not messages:
            return messages, 0

        result = [m.copy() for m in messages]
        # 构建 tool_call_id → (name, args) 索引
        call_map: dict[str, tuple[str, str]] = {}
        for msg in result:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    cid = tc.get("id", "") if isinstance(tc, dict) else ""
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    call_map[cid] = (fn.get("name", "?"), fn.get("arguments", ""))

        prune_boundary = max(0, len(result) - self.protect_first_n * 2)
        pruned = 0
        for i in range(prune_boundary):
            msg = result[i]
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not content or isinstance(content, list) or len(content) <= 200:
                continue
            cid = msg.get("tool_call_id", "")
            tool_name, args_str = call_map.get(cid, ("?", "{}"))
            result[i] = {**msg, "content": _tool_summary(tool_name, args_str, content)}
            pruned += 1

        return result, pruned

    # ── 边界保护 ──

    def _find_tail_cut(self, messages: list[dict], head_end: int) -> int:
        """从末尾向前走，按 token 预算找到尾部起点。"""
        n = len(messages)
        budget = self.tail_token_budget
        accumulated = 0
        cut_idx = n

        for i in range(n - 1, head_end - 1, -1):
            msg = messages[i]
            tokens = _content_len(msg.get("content") or "") // _CHARS_PER_TOKEN + 10
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict):
                    tokens += len(tc.get("function", {}).get("arguments", "")) // _CHARS_PER_TOKEN
            if accumulated + tokens > budget:
                break
            accumulated += tokens
            cut_idx = i

        # 至少保护 3 条消息
        return max(cut_idx, head_end + 1, n - 3)

    def _align_boundary_forward(self, messages: list[dict], idx: int) -> int:
        """跳过 tool 结果，避免从 tool 结果中间开始压缩。"""
        while idx < len(messages) and messages[idx].get("role") == "tool":
            idx += 1
        return idx

    def _align_boundary_backward(self, messages: list[dict], idx: int) -> int:
        """回退到 assistant 消息前，避免拆分 tool_call 组。"""
        if idx <= 0:
            return idx
        check = idx - 1
        while check >= 0 and messages[check].get("role") == "tool":
            check -= 1
        if check >= 0 and messages[check].get("role") == "assistant" and messages[check].get("tool_calls"):
            idx = check
        return max(idx, 0)

    # ── 摘要生成 ──

    def _serialize_for_summary(self, turns: list[dict]) -> str:
        """将中间轮次序列化为摘要模型的输入文本。"""
        parts = []
        for msg in turns:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            if role == "tool":
                parts.append(f"[TOOL RESULT]: {content[:2000]}")
            elif role == "assistant":
                tcs = msg.get("tool_calls") or []
                tc_text = ""
                if tcs:
                    tc_lines = []
                    for tc in tcs:
                        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                        tc_lines.append(f"  {fn.get('name', '?')}({fn.get('arguments', '')[:500]})")
                    tc_text = "\n[Tool calls:\n" + "\n".join(tc_lines) + "\n]"
                parts.append(f"[ASSISTANT]: {content[:3000]}{tc_text}")
            else:
                parts.append(f"[{role.upper()}]: {content[:3000]}")
        return "\n\n".join(parts)

    def _generate_summary(self, turns: list[dict]) -> str | None:
        """调 LLM 生成结构化摘要。"""
        if self._summarize is None:
            logger.warning("No summarize_fn available — skipping LLM summary")
            return None

        content = self._serialize_for_summary(turns)

        if self._previous_summary:
            prompt = f"""You are updating a context compaction summary. A previous compaction produced the summary below. New conversation turns have occurred since.

PREVIOUS SUMMARY:
{self._previous_summary}

NEW TURNS:
{content}

Update the summary. PRESERVE all existing information still relevant. ADD new completed actions. Update Active Task to reflect the latest unfulfilled request.

Use this structure:

## Active Task
[What the user most recently asked to do — be specific]

## Completed Actions
[Numbered list — include file paths, commands, results]

## Key Findings & Decisions
[Important context, decisions, errors]

## Blocked
[Any blockers or unresolved issues]

## Pending User Asks
[Questions not yet answered]

## Remaining Work
[What's left]

Target ~{min(self.summary_max_tokens, 2000)} tokens."""
        else:
            prompt = f"""You are a summarization agent. Summarize the conversation turns below into a structured handoff summary. This summary will be injected as reference for a DIFFERENT assistant continuing the conversation.

Do NOT respond to any questions in the conversation — only output the summary.
Do NOT include any preamble.
Write in the same language the user was using.
NEVER include API keys, tokens, passwords, or secrets — replace with [REDACTED].

Use this structure:

## Active Task
[What the user most recently asked to do — copy their exact words if possible]

## Completed Actions
[Numbered list of actions taken — include file paths, commands, and outcomes]

## Key Findings & Decisions
[Important technical context, decisions, errors, and discoveries]

## Blocked
[Any blockers, errors, or unresolved issues]

## Pending User Asks
[Questions the user asked that have NOT yet been addressed]

## Remaining Work
[What's left to do]

TURNS TO SUMMARIZE:
{content}

Target ~{min(self.summary_max_tokens, 2000)} tokens. Be concrete — include file paths, command outputs, and specific values."""
        try:
            result = self._summarize(prompt)
            if not result:
                return None
            self._previous_summary = result.strip()
            return f"{_SUMMARY_PREFIX}\n{self._previous_summary}"
        except Exception as e:
            logger.warning("Summary generation failed: %s", e)
            return None

    # ── 工具配对修复 ──

    def _sanitize_tool_pairs(self, messages: list[dict]) -> list[dict]:
        """修复压缩后 tool_call/tool_result 配对断裂。"""
        call_ids: set = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    cid = tc.get("id", "") if isinstance(tc, dict) else ""
                    if cid:
                        call_ids.add(cid)

        result_ids: set = set()
        for msg in messages:
            if msg.get("role") == "tool":
                cid = msg.get("tool_call_id")
                if cid:
                    result_ids.add(cid)

        # 移除孤儿 tool result
        orphaned = result_ids - call_ids
        if orphaned:
            messages = [
                m for m in messages
                if not (m.get("role") == "tool" and m.get("tool_call_id") in orphaned)
            ]

        # 添加 stub 给缺失的 tool result
        missing = call_ids - result_ids
        if missing:
            patched = []
            for msg in messages:
                patched.append(msg)
                if msg.get("role") == "assistant":
                    for tc in msg.get("tool_calls") or []:
                        cid = tc.get("id", "") if isinstance(tc, dict) else ""
                        if cid in missing:
                            patched.append({
                                "role": "tool",
                                "content": "[Result from earlier conversation — see context summary]",
                                "tool_call_id": cid,
                            })
            messages = patched

        return messages

    # ── 主入口 ──

    def compress(self, messages: list[dict]) -> list[dict]:
        n = len(messages)
        if n < self.protect_first_n + 4:
            logger.debug("Too few messages to compress (%d)", n)
            return messages

        # Phase 1: 免费裁剪 tool 结果
        messages, pruned = self._prune_tool_results(messages)
        if pruned:
            logger.info("Pruned %d old tool result(s)", pruned)

        # Phase 2: 确定边界
        head_end = self._align_boundary_forward(messages, self.protect_first_n)
        tail_start = self._find_tail_cut(messages, head_end)
        tail_start = self._align_boundary_backward(messages, tail_start)
        tail_start = max(tail_start, head_end + 1)

        if head_end >= tail_start:
            logger.debug("No compressible middle region (head=%d tail=%d)", head_end, tail_start)
            return messages

        middle = messages[head_end:tail_start]
        tail = messages[tail_start:]

        # Phase 3: LLM 摘要
        summary = self._generate_summary(middle)

        # Phase 4: 组装
        compressed = messages[:head_end]

        if summary:
            # 选择不跟首尾角色冲突的角色
            last_head_role = messages[head_end - 1].get("role", "user") if head_end > 0 else "user"
            first_tail_role = tail[0].get("role", "user") if tail else "user"
            summary_role = "assistant" if last_head_role == "user" else "user"
            if summary_role == first_tail_role:
                summary_role = "assistant" if first_tail_role == "user" else "user"
            compressed.append({"role": summary_role, "content": summary})
        else:
            # 摘要失败 → 简单截断（替代直接删除）
            logger.warning("Summary failed, dropping %d middle messages without summary", len(middle))

        compressed.extend(tail)

        # Phase 5: 修复配对
        compressed = self._sanitize_tool_pairs(compressed)

        self.compression_count += 1
        logger.info("Compressed: %d → %d messages (%d middle turns summarized)", n, len(compressed), len(middle))
        return compressed
