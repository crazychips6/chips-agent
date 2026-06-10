"""MemoryManager — 记忆提供者编排器

编排内置记忆提供者 + 至多一个外部提供者。
内置提供者始终在首位且不可移除，外部提供者只能存在一个。

所有 provider 的方法调用都受异常保护（一个失败不阻塞其他）。"""

from __future__ import annotations

import logging
from typing import Any

from memory.provider import MemoryProvider

logger = logging.getLogger("chips.memory.manager")


class MemoryManager:
    def __init__(self):
        self._providers: list[MemoryProvider] = []
        self._tool_to_provider: dict[str, MemoryProvider] = {}
        self._has_external: bool = False

    # ── 注册 ──

    def add_provider(self, provider: MemoryProvider) -> None:
        is_builtin = provider.name == "builtin"

        if not is_builtin:
            if self._has_external:
                existing = next(
                    (p.name for p in self._providers if p.name != "builtin"), "unknown"
                )
                logger.warning(
                    "拒绝外部提供者 '%s' — 已存在 '%s'，只允许一个外部提供者",
                    provider.name, existing,
                )
                return
            self._has_external = True

        self._providers.append(provider)
        for schema in provider.get_tool_schemas():
            tool_name = schema.get("name", "")
            if tool_name and tool_name not in self._tool_to_provider:
                self._tool_to_provider[tool_name] = provider

        logger.info("记忆提供者 '%s' 已注册（%d 个工具）",
                    provider.name, len(provider.get_tool_schemas()))

    @property
    def providers(self) -> list[MemoryProvider]:
        return list(self._providers)

    # ── System Prompt ──

    def snapshot(self) -> str:
        """冻结快照：仅收集「不随会话变化」的提供者静态块。

        只取 builtin 提供者的 system_prompt_block()（MEMORY.md 快照）。
        外部提供者的检索走 prefetch，不走这里。
        """
        for p in self._providers:
            if p.name == "builtin":
                try:
                    return p.system_prompt_block() or ""
                except Exception:
                    logger.debug("提供者 '%s' snapshot 失败", p.name, exc_info=True)
                    return ""
        return ""

    def build_system_prompt(self) -> str:
        """收集所有提供者的 system prompt 文本。"""
        blocks = []
        for p in self._providers:
            try:
                block = p.system_prompt_block()
                if block and block.strip():
                    blocks.append(block)
            except Exception:
                logger.debug("提供者 '%s' system_prompt_block 失败", p.name, exc_info=True)
        return "\n\n".join(blocks)

    # ── 预检索 ──

    def prefetch_all(self, query: str, *, session_id: str = "") -> str:
        """收集所有提供者的检索结果。"""
        parts = []
        for p in self._providers:
            try:
                result = p.prefetch(query, session_id=session_id)
                if result and result.strip():
                    parts.append(result)
            except Exception:
                logger.debug("提供者 '%s' prefetch 失败", p.name, exc_info=True)
        return "\n\n".join(parts)

    # ── 同步 ──

    def sync_all(self, user_content: str, assistant_content: str, *,
                 session_id: str = "") -> None:
        for p in self._providers:
            try:
                p.sync_turn(user_content, assistant_content, session_id=session_id)
            except Exception:
                logger.debug("提供者 '%s' sync_turn 失败", p.name, exc_info=True)

    # ── 工具 ──

    def get_all_tool_schemas(self) -> list[dict]:
        """收集所有提供者的工具 schema，按名称去重。"""
        schemas = []
        seen: set[str] = set()
        for p in self._providers:
            try:
                for s in p.get_tool_schemas():
                    name = s.get("name", "")
                    if name and name not in seen:
                        schemas.append(s)
                        seen.add(name)
            except Exception:
                logger.debug("提供者 '%s' get_tool_schemas 失败", p.name, exc_info=True)
        return schemas

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tool_to_provider

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        provider = self._tool_to_provider.get(tool_name)
        if not provider:
            return f'{{"error": "无提供者处理工具 {tool_name}"}}'
        try:
            result = provider.handle_tool_call(tool_name, args, **kwargs)
        except Exception as e:
            logger.error("提供者 '%s' handle_tool_call(%s) 失败: %s",
                         provider.name, tool_name, e)
            return f'{{"error": "工具 {tool_name} 执行失败: {e}"}}'

        # 内置 memory 工具写操作 → 广播给外部 provider
        if tool_name == "memory" and provider.name == "builtin":
            action = args.get("action", "")
            target = args.get("target", "memory")
            content = args.get("content", "")
            if action in ("add", "replace", "remove") and content:
                self.on_memory_write(action, target, content)

        return result

    # ── 生命周期 ──

    def initialize_all(self, session_id: str = "", **kwargs) -> None:
        for p in self._providers:
            try:
                p.initialize(session_id=session_id, **kwargs)
            except Exception:
                logger.warning("提供者 '%s' initialize 失败", p.name, exc_info=True)

    def shutdown_all(self) -> None:
        for p in reversed(self._providers):
            try:
                p.shutdown()
            except Exception:
                pass

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        for p in self._providers:
            try:
                p.on_session_end(messages)
            except Exception:
                pass

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: dict | None = None) -> None:
        for p in self._providers:
            if p.name == "builtin":
                continue
            try:
                p.on_memory_write(action, target, content, metadata=dict(metadata or {}))
            except Exception:
                pass
