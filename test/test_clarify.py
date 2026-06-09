"""clarify 工具单元测试"""

import json

import tool.builtins  # noqa: F401 — 触发所有工具注册


class TestClarifyTool:
    """测试 clarify handler（通过 registry dispatch）。"""

    def test_requires_question(self):
        from tool.registry import registry
        result = registry.dispatch("clarify", {})
        data = json.loads(result)
        assert "error" in data

    def test_empty_question(self):
        from tool.registry import registry
        result = registry.dispatch("clarify", {"question": ""})
        data = json.loads(result)
        assert "error" in data

    def test_open_ended_with_mock_callback(self):
        from tool.registry import registry
        import tool.builtins.clarify_tool

        saved = tool.builtins.clarify_tool._callback
        tool.builtins.clarify_tool._callback = lambda q, c: "用户回复"
        try:
            result = registry.dispatch("clarify", {"question": "你确定吗？"})
            data = json.loads(result)
            assert data["question"] == "你确定吗？"
            assert data["choices_offered"] is None
            assert data["user_response"] == "用户回复"
        finally:
            tool.builtins.clarify_tool._callback = saved

    def test_choices_with_mock_callback(self):
        from tool.registry import registry
        import tool.builtins.clarify_tool

        saved = tool.builtins.clarify_tool._callback
        tool.builtins.clarify_tool._callback = lambda q, c: c[0]
        try:
            result = registry.dispatch("clarify", {
                "question": "选哪个？",
                "choices": ["方案A", "方案B"],
            })
            data = json.loads(result)
            assert data["question"] == "选哪个？"
            assert data["choices_offered"] == ["方案A", "方案B"]
            assert data["user_response"] == "方案A"
        finally:
            tool.builtins.clarify_tool._callback = saved

    def test_choices_max_limit(self):
        from tool.registry import registry
        import tool.builtins.clarify_tool

        saved = tool.builtins.clarify_tool._callback
        tool.builtins.clarify_tool._callback = lambda q, c: c[-1]
        try:
            result = registry.dispatch("clarify", {
                "question": "选？",
                "choices": ["A", "B", "C", "D", "E"],
            })
            data = json.loads(result)
            assert len(data["choices_offered"]) == 4
            assert "E" not in data["choices_offered"]
        finally:
            tool.builtins.clarify_tool._callback = saved

    def test_choices_empty_list_becomes_open(self):
        from tool.registry import registry
        import tool.builtins.clarify_tool

        saved = tool.builtins.clarify_tool._callback
        tool.builtins.clarify_tool._callback = lambda q, c: "x"
        try:
            result = registry.dispatch("clarify", {
                "question": "问？",
                "choices": [],
            })
            data = json.loads(result)
            assert data["choices_offered"] is None
        finally:
            tool.builtins.clarify_tool._callback = saved

    def test_callback_error_returns_json(self):
        """回调抛出异常时返回 JSON 错误，不抛到上层。"""
        from tool.registry import registry
        import tool.builtins.clarify_tool

        saved = tool.builtins.clarify_tool._callback
        tool.builtins.clarify_tool._callback = lambda q, c: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            result = registry.dispatch("clarify", {"question": "问？"})
            data = json.loads(result)
            assert "error" in data
        finally:
            tool.builtins.clarify_tool._callback = saved
