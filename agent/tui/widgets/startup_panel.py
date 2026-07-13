"""StartupPanel — 启动面板。"""

from __future__ import annotations

from textual.widgets import Static


class StartupPanel(Static):
    """启动面板：Logo + 状态信息。"""

    def __init__(
        self,
        model: str = "",
        tool_count: int = 0,
        toolset_names: tuple | list = (),
        memory_status: str = "off",
        mcp_status: str = "off",
        skill_status: str = "off",
        compress_status: str = "on",
        context_file_count: int = 0,
        **kwargs,
    ) -> None:
        # 构建状态行
        ts = ", ".join(f"{n}" for n in toolset_names) if toolset_names else "core"
        status = f"Model: {model}  |  Tools: {tool_count} ({ts})"

        extras = []
        if memory_status != "off":
            extras.append(f"Mem: {memory_status}")
        if mcp_status != "off":
            extras.append(f"MCP: {mcp_status}")
        if skill_status != "off":
            extras.append(f"Skills: {skill_status}")
        if compress_status != "off":
            extras.append(f"Compress: {compress_status}")
        if context_file_count:
            extras.append(f"Files: {context_file_count}")

        content = status
        if extras:
            content += "\n" + "  |  ".join(extras)

        super().__init__(content, classes="startup-panel", **kwargs)
