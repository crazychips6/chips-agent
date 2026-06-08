"""MCP 集成 — 通过 Model Context Protocol 连接外部工具服务器。

MCP (Model Context Protocol) 标准化了 LLM 应用与外部工具的通信。
chips 作为 MCP Client，可以连接任意 MCP Server，自动将其工具注册到 ToolRegistry。

配置 ~/.chips/config.yaml::

    mcp_servers:
      filesystem:
        transport: stdio
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
      github:
        transport: stdio
        command: npx
        args: ["-y", "@modelcontextprotocol/server-github"]
        env:
          GITHUB_TOKEN: "${GITHUB_TOKEN}"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from concurrent.futures import Future
from typing import Any

logger = logging.getLogger("chips.plugins.mcp")


def _build_openai_schema(server_name: str, tool_name: str,
                          description: str | None,
                          input_schema: dict[str, Any]) -> dict[str, Any]:
    """将 MCP Tool 转换为 OpenAI function-calling schema。

    MCP Tool.inputSchema 已是 JSON Schema 格式，OpenAI 也使用 JSON Schema，
    所以基本是直接搬运，加上外层 function 包装。
    """
    return {
        "type": "function",
        "function": {
            "name": f"{server_name}_{tool_name}",
            "description": description or "",
            "parameters": input_schema or {"type": "object", "properties": {}},
        },
    }


class MCPClient:
    """管理单个 MCP 服务器的连接。

    在后台线程中运行 asyncio 事件循环，维护与 MCP Server 的 stdio 连接。
    通过 run_coroutine_threadsafe 实现线程安全的方法调用。
    """

    def __init__(self, name: str, command: str, *,
                 args: list[str] | None = None,
                 env: dict[str, str] | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        # 环境变量：支持 ${VAR} 语法，启动时从 os.environ 替换
        self._raw_env = env or {}

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any = None  # ClientSession
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        # 已注册的工具名列表（用于关闭时清理）
        self.registered_tools: list[str] = []

    # ── 生命周期 ──

    def start(self) -> None:
        """启动后台线程，建立与 MCP Server 的连接。

        阻塞直到 session.initialize() 完成或超时。
        """
        if self._thread and self._thread.is_alive():
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"mcp-{self.name}")
        self._thread.start()
        if not self._ready.wait(timeout=30):
            raise RuntimeError(f"MCP 服务器 '{self.name}' 30s 内未能启动")

    def _run(self) -> None:
        """后台线程入口。"""
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_session())
        except Exception:
            logger.exception("MCP server '%s' session ended with error", self.name)

    async def _run_session(self) -> None:
        """连接 MCP 服务器并维护会话。"""
        from mcp.client.stdio import stdio_client, StdioServerParameters

        env = dict(os.environ)
        for k, v in self._raw_env.items():
            env[k] = os.path.expandvars(v)

        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=env,
        )

        self._stop_event = asyncio.Event()

        async with stdio_client(server_params) as (read, write):
            async with self._create_session(read, write) as session:
                await session.initialize()
                self._session = session
                self._ready.set()
                logger.info("mcp_connected server=%s", self.name)
                await self._stop_event.wait()

    async def _create_session(self, read, write):
        """创建 ClientSession，可被子类覆写用于测试。"""
        from mcp.client.session import ClientSession
        return ClientSession(read, write)

    def stop(self) -> None:
        """关闭 MCP 连接。"""
        if self._stop_event and self._loop:
            self._stop_event.set()
            # 唤醒事件循环
            self._loop.call_soon_threadsafe(lambda: None)

    def wait_closed(self, timeout: float = 5) -> bool:
        """等待后台线程结束。超时返回 False。"""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()
        return True

    # ── 工具协议 ──

    def list_tools(self) -> list[dict[str, Any]]:
        """列出 MCP 服务器暴露的工具（同步包装）。"""
        future: Future = Future()
        asyncio.run_coroutine_threadsafe(
            self._async_list_tools(future), self._loop,
        )
        return future.result(timeout=10)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用 MCP 工具并返回结果文本（同步包装）。"""
        future: Future = Future()
        asyncio.run_coroutine_threadsafe(
            self._async_call_tool(name, arguments, future), self._loop,
        )
        # 工具调用可能较慢，给 120s 超时
        return future.result(timeout=120)

    # ── 内部 async ──

    async def _async_list_tools(self, future: Future) -> None:
        try:
            result = await self._session.list_tools()
            tools_data = []
            for t in result.tools:
                tools_data.append({
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema or {},
                })
            future.set_result(tools_data)
        except Exception as e:
            future.set_exception(e)

    async def _async_call_tool(self, name: str, arguments: dict[str, Any],
                               future: Future) -> None:
        try:
            result = await self._session.call_tool(name, arguments)
            text_parts: list[str] = []
            for block in result.content:
                if hasattr(block, "type") and block.type == "text":
                    text_parts.append(block.text)
                elif hasattr(block, "text"):
                    text_parts.append(block.text)
                else:
                    text_parts.append(str(block))
            text = "\n".join(text_parts)
            if result.isError:
                text = json.dumps({"error": text})
            future.set_result(text)
        except Exception as e:
            future.set_exception(e)


class MCPManager:
    """管理多个 MCP 服务器连接。

    职责：
    1. 解析配置中的 mcp_servers 段
    2. 启动/停止 MCPClient 实例
    3. 将 MCP 工具自动注册到 ToolRegistry
    4. MCP 工具名使用 ``{server_name}_{tool_name}`` 前缀，避免冲突
    """

    def __init__(self, registry):
        from tool.registry import ToolRegistry
        self._registry: ToolRegistry = registry
        self._clients: dict[str, MCPClient] = {}

    def load_servers(self, servers_config: dict[str, dict]) -> list[str]:
        """加载并启动 MCP 服务器配置。

        Args:
            servers_config: config.yaml 中的 ``mcp_servers`` 字典。
                ``{server_name: {command, args, env}}``

        Returns:
            成功加载的服务器名称列表。
        """
        loaded: list[str] = []
        for name, cfg in servers_config.items():
            transport = cfg.get("transport", "stdio")
            command = cfg.get("command", "")
            if not command:
                logger.warning("mcp_skip_no_command server=%s", name)
                continue
            args = cfg.get("args") or []
            env = cfg.get("env") or {}

            client = MCPClient(
                name=name,
                command=command,
                args=args,
                env=env,
            )

            try:
                client.start()
            except Exception:
                logger.exception("mcp_start_failed server=%s", name)
                continue

            # 注册工具
            try:
                tools = client.list_tools()
                tool_names = self._register_tools(name, client, tools)
                client.registered_tools = tool_names
            except Exception:
                logger.exception("mcp_register_tools_failed server=%s", name)
                client.stop()
                continue

            self._clients[name] = client
            loaded.append(name)
            logger.info("mcp_loaded server=%s tools=%d", name, len(tools))

        return loaded

    def _register_tools(self, server_name: str, client: MCPClient,
                        tools: list[dict[str, Any]]) -> list[str]:
        """将 MCP 工具注册到 ToolRegistry。"""
        registered: list[str] = []
        for tool in tools:
            tool_name = f"{server_name}_{tool['name']}"
            schema = _build_openai_schema(
                server_name=server_name,
                tool_name=tool["name"],
                description=tool.get("description"),
                input_schema=tool.get("inputSchema"),
            )
            # handler 是同步包装，内部通过 client.call_tool 调用
            handler = self._make_handler(client, tool["name"])
            self._registry.register(
                name=tool_name,
                toolset="plugin",
                schema=schema,
                handler=handler,
            )
            registered.append(tool_name)

        return registered

    @staticmethod
    def _make_handler(client: MCPClient, tool_name: str):
        """生成同步 handler，适配 ToolRegistry.dispatch 的签名。"""
        def handler(args: dict) -> str:
            return client.call_tool(tool_name, args)
        return handler

    def stop_all(self) -> None:
        """停止所有 MCP 服务器连接。"""
        for name, client in self._clients.items():
            logger.info("mcp_stopping server=%s", name)
            # 注销工具
            for tool_name in client.registered_tools:
                self._registry.deregister(tool_name)
            client.stop()

        # 等待所有线程结束
        for name, client in self._clients.items():
            if not client.wait_closed(timeout=5):
                logger.warning("mcp_stop_timeout server=%s", name)
        self._clients.clear()

    @property
    def server_count(self) -> int:
        return len(self._clients)

    @property
    def tool_count(self) -> int:
        return sum(len(c.registered_tools) for c in self._clients.values())

    def get_server_names(self) -> list[str]:
        return list(self._clients.keys())

    def get_all_tool_names(self) -> list[str]:
        """返回所有 MCP 注册的工具名。"""
        result: list[str] = []
        for c in self._clients.values():
            result.extend(c.registered_tools)
        return result
