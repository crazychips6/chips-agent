"""CassetteGateway 单元测试 — 录制/回放/匹配策略/边界情况"""

import os
import tempfile

import pytest
import yaml

from gateway.cassette import (
    CassetteGateway,
    Interaction,
    _last_user_message,
    _sanitize_messages,
    _chat_result_to_dict,
    _dict_to_chat_result,
)
from gateway.protocol import ModelGateway
from gateway.types import ChatResult


# ── 辅助：MockGateway ──

class MockGateway(ModelGateway):
    """给录制测试用的假 gateway：返回固定的 ChatResult。"""

    def __init__(self, content: str = "mock 回复", tool_calls=None,
                 usage: dict | None = None):
        self._content = content
        self._tool_calls = tool_calls
        self._usage = usage

    def chat(self, messages, model="", **kwargs):
        return ChatResult(
            content=self._content,
            tool_calls=self._tool_calls,
            usage=self._usage,
            model=model,
        )


# ── Interaction ──

class TestInteraction:
    def test_roundtrip(self):
        i = Interaction(
            request={"messages": [{"role": "user", "content": "hi"}], "model": "m1"},
            response={"content": "hello", "model": "m1"},
        )
        d = i.to_dict()
        assert d["request"]["messages"][0]["content"] == "hi"
        restored = Interaction.from_dict(d)
        assert restored.request["model"] == "m1"
        assert restored.response["content"] == "hello"


# ── 辅助函数 ──

class TestSanitizeMessages:
    def test_basic(self):
        msgs = [
            {"role": "user", "content": "你好", "extra": "忽略"},
            {"role": "assistant", "content": "你好", "tool_calls": None},
        ]
        cleaned = _sanitize_messages(msgs)
        assert len(cleaned) == 2
        assert "extra" not in cleaned[0]
        assert cleaned[1]["tool_calls"] is None  # None 保留

    def test_none_content(self):
        msgs = [{"role": "assistant", "content": None, "tool_calls": []}]
        cleaned = _sanitize_messages(msgs)
        assert cleaned[0]["content"] == ""  # None → ""


class TestChatResultDict:
    def test_roundtrip(self):
        r = ChatResult(
            content="回复",
            tool_calls=[{"id": "c1", "type": "function",
                         "function": {"name": "echo", "arguments": "{}"}}],
            reasoning_content="思考",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
            model="m1",
        )
        d = _chat_result_to_dict(r)
        restored = _dict_to_chat_result(d)
        assert restored.content == "回复"
        assert restored.tool_calls[0]["function"]["name"] == "echo"
        assert restored.reasoning_content == "思考"
        assert restored.usage["prompt_tokens"] == 10

    def test_minimal(self):
        r = ChatResult(content="hi")
        d = _chat_result_to_dict(r)
        restored = _dict_to_chat_result(d)
        assert restored.content == "hi"
        assert restored.tool_calls is None
        assert restored.reasoning_content is None


# ── CassetteGateway：录制 ──

class TestRecord:
    def test_record_basic(self):
        """录制模式：透传调用真实 gateway 并记录。"""
        real = MockGateway(content="回复A")
        gw = CassetteGateway(
            mode="record", gateway=real, path="/tmp/_test_cassette.yaml",
        )
        result = gw.chat(
            messages=[{"role": "user", "content": "你好"}],
            model="test-model",
        )

        assert result.content == "回复A"
        assert gw.interaction_count == 1
        req = gw._interactions[0].request
        assert req["model"] == "test-model"
        assert req["messages"][0]["content"] == "你好"

    def test_record_stream(self):
        """录制模式支持 stream。"""
        real = MockGateway(content="流式回复")
        gw = CassetteGateway(
            mode="record", gateway=real, path="/tmp/_test_cassette.yaml",
        )
        collected = []
        result = gw.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
            on_chunk=lambda c: collected.append(c),
        )

        assert result.content == "流式回复"
        assert gw.interaction_count == 1

    def test_record_multiple_calls(self):
        """录制模式：多次调用全部记录。"""
        real = MockGateway(content="ok")
        gw = CassetteGateway(mode="record", gateway=real, path="/tmp/_test.yaml")
        gw.chat([{"role": "user", "content": "q1"}], model="m1")
        gw.chat([{"role": "user", "content": "q2"}], model="m1")
        assert gw.interaction_count == 2

    def test_record_no_gateway_raises(self):
        """record 模式不传 gateway 报错。"""
        with pytest.raises(ValueError, match="必须提供真实 gateway"):
            CassetteGateway(mode="record", path="/tmp/_test.yaml")


# ── CassetteGateway：保存与加载 ──

class TestSaveLoad:
    def test_save_and_load(self, tmp_path):
        """录制的 cassette 可通过 load 重新加载。"""
        path = os.path.join(tmp_path, "test.yaml")
        real = MockGateway(content="回复B")
        gw = CassetteGateway(mode="record", gateway=real, path=path)
        gw.chat(
            messages=[{"role": "user", "content": "保存测试"}],
            model="m1",
        )
        saved_path = gw.save()
        assert os.path.exists(saved_path)

        # 加载回放
        loaded = CassetteGateway.load(path)
        assert loaded.interaction_count == 1
        assert loaded._meta["model"] == "m1"

    def test_save_content_integrity(self, tmp_path):
        """保存的 YAML 内容完整可读。"""
        path = os.path.join(tmp_path, "test.yaml")
        real = MockGateway(
            content="回复C",
            tool_calls=[{"id": "c1", "type": "function",
                         "function": {"name": "echo", "arguments": "{}"}}],
            usage={"prompt_tokens": 5, "completion_tokens": 10},
        )
        gw = CassetteGateway(mode="record", gateway=real, path=path)
        gw.chat([{"role": "user", "content": "hi"}], model="m1")
        gw.save()

        # 验证 YAML 可解析且内容无误
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert data["meta"]["interaction_count"] == 1
        resp = data["interactions"][0]["response"]
        assert resp["content"] == "回复C"
        assert resp["tool_calls"][0]["function"]["name"] == "echo"
        assert resp["usage"]["prompt_tokens"] == 5

    def test_load_nonexistent(self):
        """加载不存在的文件：初始化不报错，回放时因无记录而报错。"""
        gw = CassetteGateway(mode="replay", path="/tmp/_nonexistent_cassette.yaml")
        assert gw.interaction_count == 0
        with pytest.raises(RuntimeError, match="cassette 中未找到匹配"):
            gw.chat(messages=[{"role": "user", "content": "hi"}], model="m1")

    def test_load_bad_format(self, tmp_path):
        """加载格式错误的文件报错。"""
        path = os.path.join(tmp_path, "bad.yaml")
        with open(path, "w") as f:
            f.write("not_a_cassette: true\n")
        with pytest.raises(ValueError, match="cassette 文件格式错误"):
            CassetteGateway.load(path)

    def test_load_empty_on_replay_no_file(self):
        """replay 模式无文件时不抛错（使用时才匹配）。"""
        gw = CassetteGateway(mode="replay", path="/tmp/_cassette_no_interactions.yaml")
        assert gw.interaction_count == 0


# ── CassetteGateway：回放 — 精确匹配 ──

class TestReplayExact:
    def test_replay_exact_match(self, tmp_path):
        """精确匹配：相同 messages+model 返回录制的响应。"""
        path = _record_cassette(
            tmp_path, "exact_test.yaml",
            [{"role": "user", "content": "北京天气"}],
            model="m1", response_content="晴天",
        )
        gw = CassetteGateway.load(path)
        result = gw.chat(
            messages=[{"role": "user", "content": "北京天气"}],
            model="m1",
        )
        assert result.content == "晴天"

    def test_replay_diff_model_no_match(self, tmp_path):
        """不同 model 不会匹配。"""
        path = _record_cassette(
            tmp_path, "model_mismatch.yaml",
            [{"role": "user", "content": "hi"}],
            model="m1",
        )
        gw = CassetteGateway.load(path)
        with pytest.raises(RuntimeError, match="未找到匹配"):
            gw.chat(messages=[{"role": "user", "content": "hi"}], model="m2")

    def test_replay_diff_messages_no_match(self, tmp_path):
        """不同消息不会匹配。"""
        path = _record_cassette(
            tmp_path, "msg_mismatch.yaml",
            [{"role": "user", "content": "q1"}],
            model="m1",
        )
        gw = CassetteGateway.load(path)
        with pytest.raises(RuntimeError, match="未找到匹配"):
            gw.chat(messages=[{"role": "user", "content": "q2"}], model="m1")


# ── CassetteGateway：回放 — 顺序匹配 ──

class TestReplaySequential:
    def test_replay_sequential(self, tmp_path):
        """顺序匹配：按录制顺序返回。"""
        path = os.path.join(tmp_path, "seq.yaml")
        real = MockGateway(content="r1")
        gw = CassetteGateway(mode="record", gateway=real, path=path)
        gw.chat([{"role": "user", "content": "q1"}], model="m1")
        gw.chat([{"role": "user", "content": "q2"}], model="m1")
        gw.save()

        replay = CassetteGateway.load(path, strategy="sequential")
        r1 = replay.chat([], model="")  # 请求不重要
        assert r1.content == "r1"
        r2 = replay.chat([], model="")
        assert r2.content == "r1"  # MockGateway 两次都返回 r1，按顺序回放亦如此
        assert replay.interaction_count == 2

    def test_replay_sequential_exhausted(self, tmp_path):
        """顺序模式：超出记录数时报错。"""
        path = _record_cassette(
            tmp_path, "exhaust.yaml",
            [{"role": "user", "content": "hi"}],
            model="m1", response_content="ok",
        )
        gw = CassetteGateway.load(path, strategy="sequential")
        gw.chat([], model="")  # 使用第 1 条
        with pytest.raises(RuntimeError, match="cassette 耗尽"):
            gw.chat([], model="")  # 没有第 2 条了


# ── CassetteGateway：回放 — 模糊匹配 ──

class TestReplayFuzzy:
    def test_fuzzy_match_last_message(self, tmp_path):
        """模糊匹配：相同 model + 最后一条 user message。"""
        path = _record_cassette(
            tmp_path, "fuzzy.yaml",
            [{"role": "system", "content": "你是助手"},
             {"role": "user", "content": "查询天气"}],
            model="m1", response_content="晴间多云",
        )
        gw = CassetteGateway.load(path, strategy="fuzzy")
        # 虽然 system prompt 微调，但最后一条 user message 匹配
        result = gw.chat(
            messages=[{"role": "system", "content": "你是一个助手"},
                      {"role": "user", "content": "查询天气"}],
            model="m1",
        )
        assert result.content == "晴间多云"

    def test_fuzzy_no_user_message(self, tmp_path):
        """没有 user 消息时降级为精确匹配。"""
        path = _record_cassette(
            tmp_path, "no_user.yaml",
            [{"role": "system", "content": "hi"}],
            model="m1", response_content="ok",
        )
        gw = CassetteGateway.load(path, strategy="fuzzy")
        with pytest.raises(RuntimeError, match="未找到匹配"):
            gw.chat(
                messages=[{"role": "system", "content": "hello"}],
                model="m1",
            )


# ── CassetteGateway：边界情况 ──

class TestEdgeCases:
    def test_replay_tool_calls(self, tmp_path):
        """回放 tool_calls 格式正确。"""
        path = os.path.join(tmp_path, "tool.yaml")
        real = MockGateway(
            content="",
            tool_calls=[{"id": "c1", "type": "function",
                         "function": {"name": "get_weather",
                                      "arguments": '{"city":"北京"}'}}],
        )
        gw = CassetteGateway(mode="record", gateway=real, path=path)
        gw.chat([{"role": "user", "content": "天气"}], model="m1")
        gw.save()

        replay = CassetteGateway.load(path)
        result = replay.chat(
            messages=[{"role": "user", "content": "天气"}],
            model="m1",
        )
        assert result.tool_calls is not None
        assert result.tool_calls[0]["function"]["name"] == "get_weather"
        assert result.tool_calls[0]["function"]["arguments"] == '{"city":"北京"}'

    def test_replay_reasoning_content(self, tmp_path):
        """回放保留 reasoning_content。"""
        path = _record_cassette(
            tmp_path, "reasoning.yaml",
            [{"role": "user", "content": "hi"}],
            model="m1", response_content="hello",
        )
        # 手动往 cassette 写入 reasoning_content
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["interactions"][0]["response"]["reasoning_content"] = "思考过程"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

        replay = CassetteGateway.load(path)
        result = replay.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="m1",
        )
        assert result.reasoning_content == "思考过程"

    def test_record_then_replay(self, tmp_path):
        """录制 → 保存 → 回放 全流程，结果一致。"""
        path = os.path.join(tmp_path, "full.yaml")
        real = MockGateway(content="原始回复", usage={"prompt_tokens": 5, "completion_tokens": 10})
        recorder = CassetteGateway(mode="record", gateway=real, path=path)
        recorder.chat([{"role": "user", "content": "hi"}], model="m1")
        recorder.save()

        replay = CassetteGateway.load(path)
        result = replay.chat([{"role": "user", "content": "hi"}], model="m1")
        assert result.content == "原始回复"
        assert result.usage["prompt_tokens"] == 5

    def test_clear_resets(self, tmp_path):
        """clear() 清空录制记录。"""
        real = MockGateway(content="x")
        gw = CassetteGateway(mode="record", gateway=real, path="/tmp/_test.yaml")
        gw.chat([{"role": "user", "content": "q"}], model="m1")
        assert gw.interaction_count == 1
        gw.clear()
        assert gw.interaction_count == 0
        assert gw._replay_index == 0

    def test_no_match_error_message_includes_clues(self, tmp_path):
        """不匹配时错误信息包含调试线索。"""
        path = _record_cassette(
            tmp_path, "clues.yaml",
            [{"role": "user", "content": "recorded query"}],
            model="m1",
        )
        gw = CassetteGateway.load(path)
        try:
            gw.chat(messages=[{"role": "user", "content": "different query"}], model="m2")
        except RuntimeError as e:
            msg = str(e)
            assert "未找到匹配" in msg
            assert "recorded query" in msg
            assert "different query" in msg


# ── CassetteGateway：与现有 mock 集成 ──

class TestIntegrationWithMock:
    """Cassette 可以替代 MagicMock gateway，同时保留录制能力。"""

    def test_acts_as_model_gateway(self, tmp_path):
        """CassetteGateway 遵循 ModelGateway 协议。"""
        from gateway.protocol import ModelGateway

        path = _record_cassette(tmp_path, "protocol.yaml",
                                [{"role": "user", "content": "hi"}],
                                model="m1")
        gw = CassetteGateway.load(path)
        assert isinstance(gw, ModelGateway)

    def test_with_sequential_and_mock_messages(self, tmp_path):
        """sequential 模式允许完全不匹配 messages。"""
        path = _record_cassette(tmp_path, "mock_msgs.yaml",
                                [{"role": "user", "content": "real"}],
                                model="m1", response_content="ok")
        gw = CassetteGateway.load(path, strategy="sequential")
        # messages 可以是任何值
        result = gw.chat([{"role": "user", "content": "anything"}], model="any")
        assert result.content == "ok"


# ── 录制用例示例（集成测试） ──

class TestRecordReplayDemo:
    """演示+验证录制回放作为 ModelGateway 的即插即用能力。"""

    def test_gateway_swap_works(self, tmp_path):
        """CassetteGateway 可替换真实 gateway 被其他模块使用。"""
        path = _create_chat_cassette(tmp_path, [
            ([{"role": "user", "content": "你好"}],
             ChatResult(content="你好！有什么可以帮助你的？")),
        ])

        gw = CassetteGateway.load(path)
        # 验证被调用方感知不到是回放
        result = gw.chat(
            messages=[{"role": "user", "content": "你好"}],
            model="deepseek-chat",
        )
        assert result.content == "你好！有什么可以帮助你的？"
        # model 来自录制时的原始响应，不受请求 model 影响
        assert result.model == ""

    def test_compatible_with_fallback_gateway(self, tmp_path):
        """CassetteGateway 可被 FallbackGateway 包装。"""
        from gateway.fallback import FallbackGateway

        path = _create_chat_cassette(tmp_path, [
            ([{"role": "user", "content": "hi"}],
             ChatResult(content="hello")),
        ])
        cassette = CassetteGateway.load(path)
        fallback = FallbackGateway([("deepseek-chat", cassette)])

        result = fallback.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="deepseek-chat",
        )
        assert result.content == "hello"

    def test_recorded_tool_calls_carry_through(self, tmp_path):
        """含 tool_calls 的响应通过回放完整保留。"""
        path = _create_chat_cassette(tmp_path, [
            ([{"role": "user", "content": "天气"}],
             ChatResult(content="", tool_calls=[
                 {"id": "c1", "type": "function",
                  "function": {"name": "get_weather", "arguments": '{"city":"北京"}'}},
             ])),
        ])

        gw = CassetteGateway.load(path)
        result = gw.chat(
            messages=[{"role": "user", "content": "天气"}],
            model="deepseek-chat",
        )
        assert result.content == ""
        assert result.tool_calls is not None
        assert result.tool_calls[0]["function"]["arguments"] == '{"city":"北京"}'


# ── 辅助：创建录制 cassette ──

def _record_cassette(tmp_path: str, filename: str,
                     messages: list[dict],
                     model: str = "test-model",
                     response_content: str = "默认回复") -> str:
    """录制一条交互到临时 YAML 并返回路径。"""
    path = os.path.join(tmp_path, filename)
    real = MockGateway(content=response_content)
    gw = CassetteGateway(mode="record", gateway=real, path=path)
    gw.chat(messages=messages, model=model)
    gw.save()
    return path


def _create_chat_cassette(tmp_path: str,
                          interactions: list[tuple[list[dict], ChatResult]]) -> str:
    """用预定义的 request/response 对创建 cassette。"""
    import yaml

    path = os.path.join(tmp_path, "agent_cassette.yaml")
    data = {
        "meta": {
            "recorded_at": "2026-01-01T00:00:00",
            "model": "deepseek-chat",
            "interaction_count": len(interactions),
        },
        "interactions": [
            {
                "request": {"messages": msgs, "model": "deepseek-chat", "stream": False, "kwargs": {}},
                "response": _chat_result_to_dict(result),
            }
            for msgs, result in interactions
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return path
