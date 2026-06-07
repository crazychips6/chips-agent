"""ToolRegistry 单元测试"""

import json
import time
from threading import Thread, Barrier

import pytest

from tool.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register(
        name="echo",
        toolset="core",
        schema={
            "type": "function",
            "function": {
                "name": "echo",
                "description": "原样返回输入",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        },
        handler=lambda args: args.get("text", ""),
    )
    return r


class TestRegister:
    def test_basic_register(self, registry):
        assert "echo" in registry.tool_names

    def test_register_defaults(self):
        r = ToolRegistry()
        r.register(name="minimal")
        assert "minimal" in r.tool_names
        entry = r._entries["minimal"]
        assert entry.schema == {"name": "minimal"}
        assert entry.handler({}) == ""  # 默认空 handler
        assert entry.is_async is False
        assert entry.max_result_size_chars == 100_000

    def test_deregister(self, registry):
        registry.deregister("echo")
        assert "echo" not in registry.tool_names

    def test_deregister_nonexistent(self, registry):
        registry.deregister("nonexistent")  # 不应抛异常


class TestGetDefinitions:
    def test_return_schemas(self, registry):
        defs = registry.get_definitions({"echo"})
        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "echo"

    def test_filter_unknown_names(self, registry):
        defs = registry.get_definitions({"echo", "nonexistent"})
        assert len(defs) == 1

    def test_empty_toolset(self, registry):
        defs = registry.get_definitions(set())
        assert defs == []

    def test_check_fn_excludes_tool(self):
        r = ToolRegistry()
        r.register(name="hidden", check_fn=lambda: False)
        r.register(name="visible")
        defs = r.get_definitions({"hidden", "visible"})
        assert len(defs) == 1
        assert defs[0]["name"] == "visible"

    def test_check_fn_cache(self):
        r = ToolRegistry()
        call_count = 0

        def check():
            nonlocal call_count
            call_count += 1
            return True

        r.register(name="cached", check_fn=check)
        r.get_definitions({"cached"})
        r.get_definitions({"cached"})
        assert call_count == 1  # 第二次命中缓存

    def test_check_fn_cache_expiry(self):
        r = ToolRegistry()
        call_count = 0

        def check():
            nonlocal call_count
            call_count += 1
            return True

        r.register(name="expire", check_fn=check)
        r.get_definitions({"expire"})
        # 篡改缓存时间使其过期
        r._check_fn_cache["expire"] = (0.0, True)
        r.get_definitions({"expire"})
        assert call_count == 2

    def test_check_fn_fallback_after_deregister(self):
        r = ToolRegistry()
        r.register(name="to_remove", check_fn=lambda: True)
        r.get_definitions({"to_remove"})  # 填充缓存
        r.deregister("to_remove")
        # 不应在缓存中残留
        assert "to_remove" not in r._check_fn_cache


class TestDispatch:
    def test_sync_handler(self, registry):
        result = registry.dispatch("echo", {"text": "hello"})
        assert result == "hello"

    def test_unknown_tool(self, registry):
        result = registry.dispatch("unknown", {})
        data = json.loads(result)
        assert "error" in data
        assert "unknown" in data["error"]

    def test_exception_in_handler(self, registry):
        registry.register(name="crash", handler=lambda _: 1 / 0)
        result = registry.dispatch("crash", {})
        data = json.loads(result)
        assert "error" in data
        assert "ZeroDivisionError" in data["error"]

    def test_async_handler(self, registry):
        async def async_handler(args):
            return f"async:{args.get('x', '')}"

        registry.register(name="async_tool", handler=async_handler, is_async=True)
        result = registry.dispatch("async_tool", {"x": "ok"})
        assert result == "async:ok"

    def test_result_truncation(self, registry):
        registry.register(
            name="verbose",
            handler=lambda _: "x" * 100,
            max_result_size_chars=10,
        )
        result = registry.dispatch("verbose", {})
        assert len(result) == 10 + len("\n...(truncated)")
        assert result.endswith("...(truncated)")

    def test_none_result(self, registry):
        registry.register(name="none", handler=lambda _: None)
        result = registry.dispatch("none", {})
        assert result == "null"

    def test_dict_result(self, registry):
        registry.register(name="dict_tool", handler=lambda _: {"key": "value"})
        result = registry.dispatch("dict_tool", {})
        assert result == '{"key": "value"}'


class TestToolNames:
    def test_initial_empty(self):
        r = ToolRegistry()
        assert r.tool_names == set()

    def test_after_register(self, registry):
        assert "echo" in registry.tool_names

    def test_after_deregister(self, registry):
        registry.deregister("echo")
        assert registry.tool_names == set()

    def test_isolation(self, registry):
        """tool_names 返回的是副本，修改不应影响内部状态。"""
        names = registry.tool_names
        names.add("fake")
        assert "fake" not in registry.tool_names


class TestGeneration:
    def test_register_increments(self, registry):
        gen = registry._generation
        registry.register(name="another")
        assert registry._generation == gen + 1

    def test_deregister_increments(self, registry):
        gen = registry._generation
        registry.deregister("echo")
        assert registry._generation == gen + 1


class TestThreadSafety:
    def test_concurrent_register_and_read(self):
        """并发注册和读取不应死锁或数据损坏。"""
        r = ToolRegistry()
        n_threads = 20
        barrier = Barrier(n_threads, timeout=10)
        errors = []

        def worker(i):
            try:
                barrier.wait()
                r.register(name=f"tool-{i}", handler=lambda _: str(i))
                _ = r.tool_names
                _ = r.get_definitions({f"tool-{i}"})
                r.dispatch(f"tool-{i}", {})
            except Exception as e:
                errors.append(e)

        threads = [Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"并发测试失败: {errors}"
        assert len(r.tool_names) == n_threads

    def test_concurrent_read_while_write(self):
        """读操作不应被写操作阻塞太久。"""
        r = ToolRegistry()
        r.register(name="exist", handler=lambda _: "ok")
        stop = False
        read_oks = []

        def writer():
            i = 0
            while not stop:
                r.register(name=f"tmp-{i}", handler=lambda _: "")
                r.deregister(f"tmp-{i}")
                i += 1

        def reader():
            while not stop:
                defs = r.get_definitions({"exist"})
                if len(defs) != 1:
                    read_oks.append(False)
                    return
                read_oks.append(True)

        w = Thread(target=writer)
        readers = [Thread(target=reader) for _ in range(5)]
        w.start()
        for rt in readers:
            rt.start()

        time.sleep(0.5)
        stop = True
        w.join(timeout=2)
        for rt in readers:
            rt.join(timeout=2)

        assert all(read_oks), "并发读写导致数据不一致"


class TestBusinessScenario:
    """模拟实际业务流程：添加工具 → 检查相关变量是否一致。"""

    def test_add_tool_and_verify_full_flow(self):
        """注册一个工具 → tool_names / get_definitions / dispatch 三者一致。"""
        r = ToolRegistry()
        r.register(
            name="greet",
            toolset="core",
            schema={
                "type": "function",
                "function": {
                    "name": "greet",
                    "description": "打招呼",
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
            },
            handler=lambda args: f"Hello, {args.get('name', 'world')}!",
        )

        # tool_names 包含新工具
        assert "greet" in r.tool_names

        # get_definitions 返回对应 schema
        defs = r.get_definitions({"greet"})
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "greet"

        # dispatch 能正确派发
        result = r.dispatch("greet", {"name": "chips"})
        assert result == "Hello, chips!"

    def test_multiple_tools_together(self):
        """注册多个工具 → 所有工具在 tool_names / get_definitions / dispatch 中一致。"""
        r = ToolRegistry()
        tools = {
            "echo": lambda args: args.get("text", ""),
            "add": lambda args: str(args.get("x", 0) + args.get("y", 0)),
            "upper": lambda args: args.get("s", "").upper(),
        }

        for name, handler in tools.items():
            r.register(name=name, handler=handler)
            # 每注册一个，tool_names 即时更新
            assert name in r.tool_names

        # 全部注册后，tool_names 包含所有
        assert r.tool_names == {"echo", "add", "upper"}

        # get_definitions 覆盖全部
        defs = r.get_definitions({"echo", "add", "upper"})
        assert len(defs) == 3

        # 每个工具 dispatch 都正常
        assert r.dispatch("echo", {"text": "hi"}) == "hi"
        assert r.dispatch("add", {"x": 1, "y": 2}) == "3"
        assert r.dispatch("upper", {"s": "hello"}) == "HELLO"

    def test_deregister_tool_cleanup(self):
        """注销工具 → tool_names / get_definitions 不再包含。"""
        r = ToolRegistry()
        r.register(name="tmp", handler=lambda _: "temp")
        assert "tmp" in r.tool_names

        r.deregister("tmp")

        assert "tmp" not in r.tool_names
        assert r.get_definitions({"tmp"}) == []
        assert "error" in json.loads(r.dispatch("tmp", {}))

    def test_wire_flow_simulate_cli(self):
        """模拟 CLI 中创建 agent → 赋值 registry → 检查 tool_names。"""
        from agent.loop import AIAgent

        r = ToolRegistry()
        r.register(
            name="echo",
            handler=lambda args: args.get("text", ""),
        )

        agent = AIAgent(model="test-model")
        agent.registry = r
        # 模拟 CLI 中的快照赋值
        agent.tool_names = r.tool_names

        assert "echo" in agent.tool_names
        defs = agent.registry.get_definitions(agent.tool_names)
        assert len(defs) == 1

    def test_tool_names_snapshot_behavior(self):
        """验证快照方案：wiring 时拍的快照，后续注册不会影响。"""
        r = ToolRegistry()
        r.register(name="tool_a", handler=lambda _: "a")

        from agent.loop import AIAgent

        agent = AIAgent(model="test-model")
        agent.registry = r
        # wiring 时拍快照
        agent.tool_names = r.tool_names

        assert "tool_a" in agent.tool_names

        # 后续注册新工具
        r.register(name="tool_b", handler=lambda _: "b")

        # 快照中只有 wiring 时已存在的工具
        assert "tool_b" not in agent.tool_names
        assert agent.tool_names == {"tool_a"}

