"""MCP 集成测试"""
import pytest

from plugins.mcp import _build_openai_schema, MCPManager


class TestBuildOpenaiSchema:
    def test_basic_schema(self):
        schema = _build_openai_schema(
            server_name="fs",
            tool_name="read",
            description="Read a file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        )
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "fs_read"
        assert fn["description"] == "Read a file"
        assert fn["parameters"]["properties"]["path"]["type"] == "string"
        assert fn["parameters"]["required"] == ["path"]

    def test_no_description(self):
        schema = _build_openai_schema("test", "echo", None, {"type": "object"})
        assert schema["function"]["name"] == "test_echo"
        assert schema["function"]["description"] == ""

    def test_empty_input_schema(self):
        schema = _build_openai_schema("s", "t", "desc", {})
        assert schema["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_special_chars_in_names(self):
        schema = _build_openai_schema("my-server", "list-files", "list", {})
        assert schema["function"]["name"] == "my-server_list-files"


class TestMCPManager:
    def test_empty_config(self):
        mgr = MCPManager(registry=object())  # registry won't be called
        loaded = mgr.load_servers({})
        assert loaded == []
        assert mgr.server_count == 0
        assert mgr.tool_count == 0

    def test_skip_no_command(self):
        mgr = MCPManager(registry=object())
        loaded = mgr.load_servers({
            "bad": {"transport": "stdio", "command": ""},
        })
        assert loaded == []

    def test_get_all_tool_names_empty(self):
        mgr = MCPManager(registry=object())
        assert mgr.get_all_tool_names() == []

    def test_get_server_names_empty(self):
        mgr = MCPManager(registry=object())
        assert mgr.get_server_names() == []

    def test_stop_all_empty(self):
        mgr = MCPManager(registry=object())
        mgr.stop_all()  # should not raise

    def test_register_tools(self):
        """测试工具注册逻辑（不启动实际 MCP 服务器）。"""
        from tool.registry import ToolRegistry

        reg = ToolRegistry()
        mgr = MCPManager(registry=reg)

        # 手动注册工具，跳过服务器启动
        class FakeClient:
            name = "fake"
            registered_tools = []

        client = FakeClient()
        tools = [
            {"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}},
            {"name": "ping", "description": "Ping", "inputSchema": {"type": "object"}},
        ]
        registered = mgr._register_tools("test", client, tools)  # type: ignore
        assert len(registered) == 2
        assert "test_echo" in registered
        assert "test_ping" in registered
        assert reg.tool_names == {"test_echo", "test_ping"}

        # 验证 schema 正确注册
        defs = reg.get_definitions({"test_echo", "test_ping"})
        names = [d["function"]["name"] for d in defs]
        assert "test_echo" in names
        assert "test_ping" in names

    def test_make_handler_is_callable(self):
        mgr = MCPManager(registry=object())  # type: ignore

        class FakeClient:
            name = "f"
            registered_tools = []
            def call_tool(self, name, args):
                return f"{name}:{args}"

        handler = mgr._make_handler(FakeClient(), "echo")  # type: ignore
        result = handler({"text": "hello"})
        assert result == "echo:{'text': 'hello'}"
