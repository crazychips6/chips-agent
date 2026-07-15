"""CassetteEvaluator 单元测试 — 结构化断言/诊断/边界"""

import json
import os
from unittest.mock import MagicMock

import pytest
import yaml

from gateway.evaluate import (
    CassetteEvaluator,
    CheckResult,
    EvaluationResult,
    ExpectRules,
    extract_trace,
    _is_subsequence,
    _safe_parse_json,
    print_trace,
)


# ── 工具函数测试 ──

class TestSafeParseJson:
    def test_valid_json(self):
        assert _safe_parse_json('{"a": 1}') == {"a": 1}

    def test_invalid_json(self):
        assert _safe_parse_json("{bad}") == {}

    def test_none(self):
        assert _safe_parse_json(None) == {}


class TestIsSubsequence:
    def test_exact(self):
        assert _is_subsequence(["a", "b"], ["a", "b", "c"]) is True

    def test_interleaved(self):
        assert _is_subsequence(["a", "c"], ["a", "b", "c"]) is True

    def test_not_found(self):
        assert _is_subsequence(["a", "d"], ["a", "b", "c"]) is False

    def test_wrong_order(self):
        assert _is_subsequence(["b", "a"], ["a", "b", "c"]) is False

    def test_empty_seq(self):
        assert _is_subsequence([], ["a", "b"]) is True


class TestExpectRules:
    def test_empty(self):
        rules = ExpectRules.from_dict(None)
        assert rules.final_contains == []
        assert rules.tool_min_count is None

    def test_full(self):
        rules = ExpectRules.from_dict({
            "final_output": {"contains": ["晴天"], "not_contains": ["错误"]},
            "tool_calls": {
                "min_count": 1, "max_count": 3,
                "names": ["get_weather"],
                "args_match": {"get_weather": {"city": "北京"}},
            },
            "max_llm_calls": 10,
        })
        assert rules.final_contains == ["晴天"]
        assert rules.final_not_contains == ["错误"]
        assert rules.tool_min_count == 1
        assert rules.tool_names == ["get_weather"]
        assert rules.tool_args_match["get_weather"]["city"] == "北京"
        assert rules.max_llm_calls == 10


# ── CheckResult / EvaluationResult ──

class TestCheckResult:
    def test_defaults(self):
        c = CheckResult(name="测试", passed=True)
        assert c.passed is True
        assert c.expected == ""

    def test_failure(self):
        c = CheckResult(name="测试", passed=False, expected="晴天", actual="下雨")
        assert c.passed is False


class TestEvaluationResult:
    def test_all_pass(self):
        r = EvaluationResult(
            passed=True, checks=[CheckResult("a", True)],
            total=1, passed_count=1, failed_count=0,
        )
        assert r.summary.startswith("✓")
        assert r.passed is True

    def test_has_failures(self):
        r = EvaluationResult(
            passed=False, checks=[CheckResult("a", False)],
            total=1, passed_count=0, failed_count=1,
            diagnosis="输出不正确",
        )
        assert r.summary.startswith("✗")
        assert r.passed is False
        assert r.diagnosis == "输出不正确"

    def test_print_report_passed(self, capsys):
        r = EvaluationResult(passed=True, checks=[CheckResult("a", True)], total=1, passed_count=1, failed_count=0)
        assert r.print_report() is True

    def test_print_report_failed(self, capsys):
        r = EvaluationResult(
            passed=False,
            checks=[CheckResult("输出含晴天", False, expected="晴天", actual="下雨")],
            total=1, passed_count=0, failed_count=1,
            diagnosis="缺少关键词",
        )
        assert r.print_report() is False
        captured = capsys.readouterr().out
        assert "缺少关键词" in captured
        assert "晴天" in captured
        assert "下雨" in captured


# ── extract_trace ──

class TestExtractTrace:
    def test_empty_agent(self):
        agent = MagicMock()
        agent.messages = []
        agent.debug_context = None
        trace = extract_trace(agent)
        assert trace["final_output"] == ""
        assert trace["tool_calls"] == []

    def test_with_messages(self):
        agent = MagicMock()
        agent.messages = [
            {"role": "user", "content": "天气怎么样？"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "get_weather", "arguments": '{"city":"北京"}'}}
            ]},
            {"role": "tool", "content": "晴天", "tool_call_id": "c1"},
            {"role": "assistant", "content": "北京晴天"},
        ]
        agent.debug_context = None
        agent.session_db = None
        trace = extract_trace(agent)
        assert trace["final_output"] == "北京晴天"
        assert len(trace["tool_calls"]) == 1
        assert trace["tool_calls"][0]["name"] == "get_weather"

    def test_tool_without_args(self):
        """工具无 args 时返回空 dict。"""
        agent = MagicMock()
        agent.messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "ping", "arguments": "invalid json {"}}
            ]},
        ]
        agent.debug_context = None
        agent.session_db = None
        trace = extract_trace(agent)
        assert trace["tool_calls"][0]["args"] == {}


# ── 创建测试 cassette 的辅助 ──

def _make_cassette(path: str, expect: dict | None = None,
                   interactions: list | None = None):
    """快速创建带 expect 的 cassette YAML。"""
    data = {
        "meta": {
            "recorded_at": "2026-01-01",
            "model": "test-model",
        },
        "interactions": interactions or [
            {"request": {"messages": [{"role": "user", "content": "hi"}], "model": "test-model"},
             "response": {"content": "hello"}},
        ],
    }
    if expect:
        data["meta"]["expect"] = expect
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return path


def _make_trace(final_output: str = "北京晴天",
                tool_calls: list | None = None,
                llm_calls: list | None = None,
                latency_ms: int = 1000) -> dict:
    return {
        "final_output": final_output,
        "tool_calls": tool_calls or [],
        "llm_calls": llm_calls or [],
        "latency_ms": latency_ms,
        "messages": [],
    }


# ── CassetteEvaluator 基本 ──

class TestCassetteEvaluatorInit:
    def test_load_valid(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "valid.yaml"))
        e = CassetteEvaluator(p)
        assert e.expect is not None

    def test_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            CassetteEvaluator("/tmp/_nonexistent_eval.yaml")

    def test_bad_format(self, tmp_path):
        p = os.path.join(tmp_path, "bad.yaml")
        with open(p, "w") as f:
            f.write("not_a_cassette: true\n")
        with pytest.raises(ValueError, match="cassette 文件格式错误"):
            CassetteEvaluator(p)


# ── 最终输出断言 ──

class TestEvaluateFinalOutput:
    def test_contains_pass(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "c1.yaml"), {
            "final_output": {"contains": ["晴天", "北京"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace("北京明天晴天"))
        assert r.passed is True
        assert r.passed_count == 2

    def test_contains_fail(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "c2.yaml"), {
            "final_output": {"contains": ["暴雨"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace("北京晴天"))
        assert r.passed is False
        assert r.failed_count == 1
        assert "暴雨" in r.checks[0].name

    def test_not_contains_pass(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "c3.yaml"), {
            "final_output": {"not_contains": ["错误", "异常"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace("一切正常"))
        assert r.passed is True

    def test_not_contains_fail(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "c4.yaml"), {
            "final_output": {"not_contains": ["错误"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace("发生错误"))
        assert r.passed is False

    def test_regex_pass(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "c5.yaml"), {
            "final_output": {"regex": "北京.*(晴|多云)"},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace("北京今天晴间多云"))
        assert r.passed is True

    def test_regex_fail(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "c6.yaml"), {
            "final_output": {"regex": "北京.*(晴|多云)"},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace("上海今天下雨"))
        assert r.passed is False

    def test_exact_match(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "c7.yaml"), {
            "final_output": {"exact": "北京晴天"},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace("北京晴天"))
        assert r.passed is True
        r2 = e.evaluate_trace(_make_trace("北京晴天 "))
        assert r2.passed is False


# ── 工具调用断言 ──

class TestEvaluateToolCalls:
    def test_min_count_pass(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "t1.yaml"), {
            "tool_calls": {"min_count": 1},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[
            {"name": "get_weather", "args": {"city": "北京"}, "step": 1},
        ]))
        assert r.passed is True

    def test_min_count_fail(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "t2.yaml"), {
            "tool_calls": {"min_count": 1},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[]))
        assert r.passed is False

    def test_max_count_fail(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "t3.yaml"), {
            "tool_calls": {"max_count": 2},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[
            {"name": "a", "args": {}, "step": 1},
            {"name": "b", "args": {}, "step": 2},
            {"name": "c", "args": {}, "step": 3},
        ]))
        assert r.passed is False
        assert "≤ 2" in r.checks[0].expected

    def test_names_pass(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "t4.yaml"), {
            "tool_calls": {"names": ["get_weather"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[
            {"name": "get_weather", "args": {}, "step": 1},
        ]))
        assert r.passed is True

    def test_names_fail(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "t5.yaml"), {
            "tool_calls": {"names": ["get_weather"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[
            {"name": "search_web", "args": {}, "step": 1},
        ]))
        assert r.passed is False

    def test_sequence_pass(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "t6.yaml"), {
            "tool_calls": {"sequence_contains": ["get_weather", "send_email"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[
            {"name": "get_weather", "args": {}, "step": 1},
            {"name": "search_web", "args": {}, "step": 2},
            {"name": "send_email", "args": {}, "step": 3},
        ]))
        assert r.passed is True

    def test_sequence_fail_wrong_order(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "t7.yaml"), {
            "tool_calls": {"sequence_contains": ["send_email", "get_weather"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[
            {"name": "get_weather", "args": {}, "step": 1},
            {"name": "send_email", "args": {}, "step": 2},
        ]))
        assert r.passed is False

    def test_args_match_pass(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "t8.yaml"), {
            "tool_calls": {"args_match": {"get_weather": {"city": "北京"}}},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[
            {"name": "get_weather", "args": {"city": "北京", "unit": "celsius"}, "step": 1},
        ]))
        assert r.passed is True

    def test_args_match_fail(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "t9.yaml"), {
            "tool_calls": {"args_match": {"get_weather": {"city": "上海"}}},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[
            {"name": "get_weather", "args": {"city": "北京"}, "step": 1},
        ]))
        assert r.passed is False
        assert "上海" in r.checks[0].expected

    def test_args_match_tool_not_called(self, tmp_path):
        """指定工具未调用时参数检查失败。"""
        p = _make_cassette(os.path.join(tmp_path, "t10.yaml"), {
            "tool_calls": {"args_match": {"get_weather": {"city": "北京"}}},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[
            {"name": "search_web", "args": {}, "step": 1},
        ]))
        assert r.passed is False
        assert "未调用" in r.checks[0].actual


# ── 性能断言 ──

class TestEvaluatePerformance:
    def test_max_llm_calls_pass(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "p1.yaml"), {
            "max_llm_calls": 5,
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(llm_calls=[{}, {}, {}]))
        assert r.passed is True

    def test_max_llm_calls_fail(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "p2.yaml"), {
            "max_llm_calls": 2,
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(llm_calls=[{}, {}, {}]))
        assert r.passed is False


# ── 完整场景：综合断言 ──

class TestEvaluateFullScenario:
    def test_all_pass_good_weather(self, tmp_path):
        """完整的天气查询场景，所有断言通过。"""
        p = _make_cassette(os.path.join(tmp_path, "full_pass.yaml"), {
            "final_output": {"contains": ["晴天", "北京"]},
            "tool_calls": {
                "min_count": 1,
                "max_count": 3,
                "names": ["get_weather"],
                "sequence_contains": ["get_weather"],
                "args_match": {"get_weather": {"city": "北京"}},
            },
            "max_llm_calls": 5,
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(
            final_output="北京今天晴天，气温 25-32°C",
            tool_calls=[{"name": "get_weather", "args": {"city": "北京"}, "step": 1}],
            llm_calls=[{"response_content": "", "response_tool_calls": [
                {"function": {"name": "get_weather"}}
            ]}, {"response_content": "北京今天晴天..."}],
        ))
        assert r.passed is True
        assert r.passed_count == r.total

    def test_wrong_tool_fails(self, tmp_path):
        """调用了错误的工具。"""
        p = _make_cassette(os.path.join(tmp_path, "full_fail_tool.yaml"), {
            "final_output": {"contains": ["晴天"]},
            "tool_calls": {
                "names": ["get_weather"],
                "args_match": {"get_weather": {"city": "北京"}},
            },
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(
            final_output="北京晴天",
            tool_calls=[{"name": "search_web", "args": {"q": "北京天气"}, "step": 1}],
        ))
        assert r.passed is False
        # 工具名称检查失败，但最终输出检查通过
        assert r.checks[0].passed is True  # 输出含"晴天"
        assert r.checks[-1].passed is False  # 参数检查失败（工具名不对）

    def test_output_missing_keyword(self, tmp_path):
        """输出缺少关键词。"""
        p = _make_cassette(os.path.join(tmp_path, "full_fail_output.yaml"), {
            "final_output": {"contains": ["晴天", "北京"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace("上海下雨"))
        assert r.passed is False
        diagnosis = r.diagnosis
        assert "最终输出" in diagnosis
        assert "晴天" in diagnosis or "北京" in diagnosis


# ── 诊断信息 ──

class TestDiagnosis:
    def test_diagnosis_contains_output_hint(self, tmp_path):
        """缺少关键词时诊断提示。"""
        p = _make_cassette(os.path.join(tmp_path, "d1.yaml"), {
            "final_output": {"contains": ["晴天"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace("下雨了"))
        assert "晴天" in r.diagnosis

    def test_diagnosis_tool_call_sequence(self, tmp_path):
        """工具调用顺序错误时诊断列出实际顺序。"""
        p = _make_cassette(os.path.join(tmp_path, "d2.yaml"), {
            "tool_calls": {"sequence_contains": ["a", "b"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(tool_calls=[
            {"name": "b", "args": {}, "step": 1},
            {"name": "a", "args": {}, "step": 2},
        ]))
        assert "工具" in r.diagnosis
        assert "'b'" in r.diagnosis or "b" in r.diagnosis

    def test_diagnosis_tool_error_in_messages(self, tmp_path):
        """工具执行报错时诊断提示。"""
        agent = MagicMock()
        agent.messages = [
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "bad_tool", "arguments": "{}"}},
            ]},
            {"role": "tool", "content": '{"error": "超时"}', "tool_call_id": "c1"},
            {"role": "assistant", "content": "出错了"},
        ]
        agent.debug_context = None
        agent.session_db = None
        # 加一个会失败的预期规则，触发诊断路径
        p = _make_cassette(os.path.join(tmp_path, "d3.yaml"), {
            "final_output": {"contains": ["成功"]},
        })
        e = CassetteEvaluator(p)
        r = e.evaluate(agent)
        assert r.passed is False
        # 诊断应包含输出缺少关键词的提示；额外检查 trace 中包含工具错误信息
        assert "成功" in r.diagnosis or "输出" in r.diagnosis
        # 验证 trace 中收集到了工具的错误信息
        assert any("超时" in str(m.get("content", ""))
                   for m in r.trace.get("messages", []) if m.get("role") == "tool")

    def test_diagnosis_llm_too_many_calls(self, tmp_path):
        """LLM 调用过多时诊断。"""
        p = _make_cassette(os.path.join(tmp_path, "d4.yaml"), {
            "max_llm_calls": 2,
        })
        e = CassetteEvaluator(p)
        r = e.evaluate_trace(_make_trace(llm_calls=[{}, {}, {}, {}]))
        assert "LLM" in r.diagnosis
        assert "4" in r.diagnosis


# ── evaluate_interactions 逐步骤对比 ──

class TestEvaluateInteractions:
    def test_equal_length(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "i1.yaml"))
        e = CassetteEvaluator(p)
        exp = [
            {"request": {"messages": [], "model": "m"},
             "response": {"tool_calls": []}},
            {"request": {"messages": [], "model": "m"},
             "response": {"tool_calls": [{"function": {"name": "echo"}}]}},
        ]
        act = [
            {"request": {"messages": [], "model": "m"},
             "response": {"tool_calls": []}},
            {"request": {"messages": [], "model": "m"},
             "response": {"tool_calls": [{"function": {"name": "echo"}}]}},
        ]
        r = e.evaluate_interactions(exp, act)
        assert r.passed is True

    def test_tool_mismatch(self, tmp_path):
        p = _make_cassette(os.path.join(tmp_path, "i2.yaml"))
        e = CassetteEvaluator(p)
        exp = [
            {"request": {"messages": [], "model": "m"},
             "response": {"tool_calls": [{"function": {"name": "get_weather"}}]}},
        ]
        act = [
            {"request": {"messages": [], "model": "m"},
             "response": {"tool_calls": [{"function": {"name": "search_web"}}]}},
        ]
        r = e.evaluate_interactions(exp, act)
        assert r.passed is False
        assert "搜索" in r.checks[0].actual or "search_web" in r.checks[0].actual


# ── print_trace ──

class TestPrintTrace:
    def test_empty(self, capsys):
        print_trace({"final_output": "", "llm_calls": [], "tool_calls": []})
        captured = capsys.readouterr().out
        assert "Trace" in captured

    def test_with_tool_calls(self, capsys):
        print_trace({
            "final_output": "北京晴天",
            "llm_calls": [{"response_content": "hello", "response_tool_calls": None}],
            "tool_calls": [{"name": "get_weather", "args": {"city": "北京"}, "step": 1}],
        })
        captured = capsys.readouterr().out
        assert "北京" in captured
        assert "get_weather" in captured
