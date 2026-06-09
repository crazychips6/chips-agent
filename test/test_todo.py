"""todo 工具单元测试"""
import json

from tool.builtins.todo_tool import TodoStore


class TestTodoStore:
    """Test TodoStore directly (no registry dependency)."""

    def test_read_empty(self):
        store = TodoStore()
        assert store.read() == []
        assert not store.has_items()

    def test_write_and_read(self):
        store = TodoStore()
        store.write([
            {"id": "1", "content": "任务一", "status": "pending"},
            {"id": "2", "content": "任务二", "status": "in_progress"},
        ])
        items = store.read()
        assert len(items) == 2
        assert items[0]["content"] == "任务一"
        assert items[1]["status"] == "in_progress"

    def test_write_replaces(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "旧任务", "status": "pending"}])
        store.write([{"id": "2", "content": "新任务", "status": "pending"}])
        assert len(store.read()) == 1
        assert store.read()[0]["id"] == "2"

    def test_merge_adds_new(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "A", "status": "pending"}])
        store.write([{"id": "2", "content": "B", "status": "pending"}], merge=True)
        assert len(store.read()) == 2

    def test_merge_updates_existing(self):
        store = TodoStore()
        store.write([{"id": "1", "content": "A", "status": "pending"}])
        store.write([{"id": "1", "content": "A 已更新", "status": "completed"}], merge=True)
        items = store.read()
        assert len(items) == 1
        assert items[0]["status"] == "completed"
        assert items[0]["content"] == "A 已更新"

    def test_validation_defaults(self):
        store = TodoStore()
        store.write([{"id": "", "content": "", "status": "invalid"}])
        items = store.read()
        assert items[0]["id"] == "?"
        assert items[0]["content"] == "(no description)"
        assert items[0]["status"] == "pending"

    def test_dedupe_by_id(self):
        """Same id keeps the last occurrence."""
        store = TodoStore()
        store.write([
            {"id": "1", "content": "A", "status": "pending"},
            {"id": "1", "content": "B", "status": "completed"},
            {"id": "2", "content": "C", "status": "pending"},
        ])
        items = store.read()
        assert len(items) == 2
        assert items[0]["content"] == "B"

    def test_handler_via_registry(self):
        """Integration: todo tool works via registry."""
        from tool.registry import registry

        # Need a store for the handler
        store = TodoStore()
        from tool.builtins.todo_tool import _store as module_store
        original = module_store
        import tool.builtins.todo_tool
        tool.builtins.todo_tool._store = store
        try:
            result = registry.dispatch("todo", {})
            data = json.loads(result)
            assert data["todos"] == []
            assert data["summary"]["total"] == 0

            result = registry.dispatch("todo", {"todos": [
                {"id": "a", "content": "test", "status": "pending"},
            ]})
            data = json.loads(result)
            assert data["summary"]["total"] == 1
            assert data["todos"][0]["content"] == "test"
        finally:
            tool.builtins.todo_tool._store = original
