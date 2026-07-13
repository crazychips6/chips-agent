"""AgentWorker — 线程池桥接 agent 调用到 Textual async 循环。"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from agent.loop import AIAgent

logger = logging.getLogger("chips.tui.worker")


class AgentWorker:
    """桥接 agent.run_conversation() 到 Textual Worker。

    agent 调用是同步阻塞的，需要在后台线程中运行，
    通过回调将 chunk/tool 事件传回 UI 线程。
    """

    def __init__(self, agent: AIAgent) -> None:
        self.agent = agent
        self._executor = ThreadPoolExecutor(max_workers=1)

    async def run(
        self,
        text: str,
        *,
        on_chunk: Callable[[str], None],
        on_tool: Callable[[str, dict, str | None], None],
    ) -> str | None:
        """异步包装：在线程池中运行 agent，回调通过 post_message 发送。"""
        loop = asyncio.get_event_loop()

        def _blocking() -> str | None:
            try:
                return self.agent.run_conversation(
                    text,
                    chunk_callback=on_chunk,
                    tool_callback=on_tool,
                )
            except Exception:
                logger.exception("agent.run_conversation failed")
                return None

        return await loop.run_in_executor(self._executor, _blocking)
