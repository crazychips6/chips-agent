"""测试 Rules Engine — RuleEngine 全局链路"""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from rules.engine import RuleEngine
from rules.facts import extract_facts, FactRegistry
from rules.loader import load_rules, write_default_config, _parse_condition_tree
from rules.models import (
    GroupCondition,
    LeafCondition,
    RouteDecision,
    parse_action,
)


# ── Models 测试 ──


class TestParseAction:
    def test_direct(self):
        assert parse_action("direct") == ("direct", "")

    def test_block(self):
        assert parse_action("block") == ("block", "")

    def test_delegate_with_target(self):
        assert parse_action("delegate(researcher)") == ("delegate", "researcher")

    def test_handoff_with_target(self):
        assert parse_action("handoff(support_agent)") == ("handoff", "support_agent")

    def test_llm_router(self):
        assert parse_action("llm_router") == ("llm_router", "")

    def test_invalid_action(self):
        with pytest.raises(ValueError, match="未知动作"):
            parse_action("nonexistent_tool")

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="无效的动作格式"):
            parse_action("")


class TestLeafCondition:
    def test_eq_match(self):
        c = LeafCondition(field="message_length", op="eq", value=10)
        assert c.evaluate({"message_length": 10})

    def test_eq_not_match(self):
        c = LeafCondition(field="message_length", op="eq", value=10)
        assert not c.evaluate({"message_length": 20})

    def test_in_match(self):
        c = LeafCondition(field="intent", op="in", value=["search", "research"])
        assert c.evaluate({"intent": "research"})

    def test_in_not_match(self):
        c = LeafCondition(field="intent", op="in", value=["search"])
        assert not c.evaluate({"intent": "coding"})

    def test_gt_match(self):
        c = LeafCondition(field="message_length", op="gt", value=100)
        assert c.evaluate({"message_length": 200})

    def test_gt_not_match(self):
        c = LeafCondition(field="message_length", op="gt", value=100)
        assert not c.evaluate({"message_length": 50})

    def test_gte_match(self):
        c = LeafCondition(field="message_length", op="gte", value=100)
        assert c.evaluate({"message_length": 100})

    def test_lt_match(self):
        c = LeafCondition(field="message_length", op="lt", value=20)
        assert c.evaluate({"message_length": 10})

    def test_lte_match(self):
        c = LeafCondition(field="message_length", op="lte", value=20)
        assert c.evaluate({"message_length": 20})

    def test_exists_match(self):
        c = LeafCondition(field="intent", op="exists")
        assert c.evaluate({"intent": ["research"]})

    def test_exists_not_match(self):
        c = LeafCondition(field="missing_field", op="exists")
        assert not c.evaluate({"intent": ["research"]})

    def test_contains_match(self):
        c = LeafCondition(field="text", op="contains", value="search")
        assert c.evaluate({"text": "please search for this"})

    def test_contains_not_match(self):
        c = LeafCondition(field="text", op="contains", value="search")
        assert not c.evaluate({"text": "hello world"})

    def test_match_regex(self):
        c = LeafCondition(field="text", op="match", value=r"帮我.*搜索")
        assert c.evaluate({"text": "帮我搜索一下 AI 框架"})

    def test_missing_field(self):
        c = LeafCondition(field="nonexistent", op="eq", value=True)
        assert not c.evaluate({})


class TestGroupCondition:
    def test_all_pass(self):
        c = GroupCondition(op="all", children=[
            LeafCondition(field="a", op="eq", value=1),
            LeafCondition(field="b", op="eq", value=2),
        ])
        assert c.evaluate({"a": 1, "b": 2})

    def test_all_fail(self):
        c = GroupCondition(op="all", children=[
            LeafCondition(field="a", op="eq", value=1),
            LeafCondition(field="b", op="eq", value=3),
        ])
        assert not c.evaluate({"a": 1, "b": 2})

    def test_any_pass(self):
        c = GroupCondition(op="any", children=[
            LeafCondition(field="a", op="eq", value=1),
            LeafCondition(field="b", op="eq", value=3),
        ])
        assert c.evaluate({"a": 1, "b": 2})

    def test_any_all_fail(self):
        c = GroupCondition(op="any", children=[
            LeafCondition(field="a", op="eq", value=5),
            LeafCondition(field="b", op="eq", value=3),
        ])
        assert not c.evaluate({"a": 1, "b": 2})

    def test_not_inverts(self):
        c = GroupCondition(op="not", children=[
            LeafCondition(field="a", op="eq", value=True),
        ])
        assert c.evaluate({"a": False})
        assert not c.evaluate({"a": True})

    def test_nested_all_any(self):
        """测试嵌套组合：all(intent in search, any(len >= 50, has_code))"""
        c = GroupCondition(op="all", children=[
            LeafCondition(field="intent", op="in", value=["search"]),
            GroupCondition(op="any", children=[
                LeafCondition(field="message_length", op="gte", value=50),
                LeafCondition(field="has_code_block", op="eq", value=True),
            ]),
        ])
        assert c.evaluate({"intent": "search", "message_length": 100, "has_code_block": False})
        assert c.evaluate({"intent": "search", "message_length": 10, "has_code_block": True})
        assert not c.evaluate({"intent": "search", "message_length": 10, "has_code_block": False})
        assert not c.evaluate({"intent": "coding", "message_length": 100, "has_code_block": False})

    def test_empty_all_passes(self):
        c = GroupCondition(op="all", children=[])
        assert c.evaluate({})

    def test_empty_any_fails(self):
        c = GroupCondition(op="any", children=[])
        assert not c.evaluate({})


class TestRouteDecision:
    def test_direct(self):
        d = RouteDecision(action="direct")
        assert not d.is_route()
        assert not d.is_block()

    def test_block(self):
        d = RouteDecision(action="block")
        assert d.is_block()

    def test_delegate_is_route(self):
        d = RouteDecision(action="delegate", target="researcher")
        assert d.is_route()

    def test_handoff_is_route(self):
        d = RouteDecision(action="handoff", target="support")
        assert d.is_route()

    def test_orchestrate_is_route(self):
        d = RouteDecision(action="orchestrate")
        assert d.is_route()

    def test_str_direct(self):
        d = RouteDecision(action="direct", reason="无匹配规则")
        assert "direct" in str(d)

    def test_str_delegate(self):
        d = RouteDecision(action="delegate", target="researcher",
                          reason="搜索意图", matched_rule="research_trigger")
        s = str(d)
        assert "delegate" in s
        assert "researcher" in s
        assert "research_trigger" in s


# ── Facts 测试 ──


class TestFactExtraction:
    def test_message_length(self):
        facts = extract_facts("你好")
        assert facts["message_length"] == 2

        facts = extract_facts("a" * 1000)
        assert facts["message_length"] == 1000

    def test_has_code_block(self):
        assert extract_facts("这是一个 ```code block```")["has_code_block"]
        assert not extract_facts("没有代码块")["has_code_block"]

    def test_question_count(self):
        assert extract_facts("这是什么？为什么？")["question_count"] == 2
        assert extract_facts("你好")["question_count"] == 0

    def test_is_short_reply(self):
        assert extract_facts("好的")["is_short_reply"]
        assert extract_facts("谢谢")["is_short_reply"]
        assert extract_facts("ok")["is_short_reply"]
        assert not extract_facts("帮我搜索一下最新的 AI 框架")["is_short_reply"]

    def test_mentions_file(self):
        assert extract_facts("帮我读取一下这个文件")["mentions_file"]
        assert extract_facts("读取文件")["mentions_file"]
        assert extract_facts("查看文档")["mentions_file"]
        assert extract_facts("写入配置到文件")["mentions_file"]
        assert not extract_facts("你好")["mentions_file"]

    def test_intent_keyword_research(self):
        facts = extract_facts("帮我搜索一下最新的 AI 框架")
        assert "research" in facts["intent_keyword"]

    def test_intent_keyword_coding(self):
        facts = extract_facts("帮我写一个 Python 脚本")
        assert "coding" in facts["intent_keyword"]

    def test_intent_keyword_file(self):
        facts = extract_facts("读取这个文件的内容")
        assert "file_operation" in facts["intent_keyword"]

    def test_intent_keyword_review(self):
        facts = extract_facts("帮我审查一下这段代码")
        assert "review" in facts["intent_keyword"]

    def test_intent_multiple(self):
        facts = extract_facts("搜索一下最新的框架，然后分析它")
        assert "research" in facts["intent_keyword"]
        assert "review" in facts["intent_keyword"]

    def test_mentions_tool_with_names(self):
        facts = extract_facts("使用 web_search 工具搜索一下",
                              tool_names={"web_search", "web_fetch", "read"})
        assert "web_search" in facts.get("mentions_tool", [])

    def test_mentions_tool_empty_without_names(self):
        facts = extract_facts("使用 web_search 工具搜索一下")
        assert facts.get("mentions_tool") == []

    def test_task_complexity_multi_step(self):
        facts = extract_facts("首先搜索资料，然后分析结果，最后写报告", cost="medium")
        signals = facts.get("task_complexity_signals", {})
        assert signals.get("has_multi_step")

    def test_task_complexity_cross_domain(self):
        facts = extract_facts("搜索最新的 AI 框架，编写示例代码，并写文档", cost="medium")
        signals = facts.get("task_complexity_signals", {})
        assert signals.get("cross_domain")


# ── Loader 测试 ──


class TestConditionParsing:
    def test_simple(self):
        c = _parse_condition_tree({"message_length": {"lt": 20}})
        assert isinstance(c, LeafCondition)
        assert c.field == "message_length"
        assert c.op == "lt"
        assert c.value == 20

    def test_all_group(self):
        c = _parse_condition_tree({
            "all": [
                {"message_length": {"gte": 20}},
                {"has_code_block": {"eq": True}},
            ],
        })
        assert isinstance(c, GroupCondition)
        assert c.op == "all"
        assert len(c.children) == 2
        assert c.children[0].field == "message_length"
        assert c.children[1].field == "has_code_block"

    def test_any_group(self):
        c = _parse_condition_tree({
            "any": [
                {"intent": {"in": ["search"]}},
                {"message_length": {"gte": 50}},
            ],
        })
        assert isinstance(c, GroupCondition)
        assert c.op == "any"
        assert len(c.children) == 2

    def test_not_group(self):
        c = _parse_condition_tree({
            "not": {"is_short_reply": {"eq": True}},
        })
        assert isinstance(c, GroupCondition)
        assert c.op == "not"
        assert len(c.children) == 1
        assert c.children[0].field == "is_short_reply"

    def test_nested(self):
        c = _parse_condition_tree({
            "all": [
                {"intent": {"in": ["search"]}},
                {
                    "any": [
                        {"message_length": {"gte": 50}},
                        {"has_code_block": {"eq": True}},
                    ],
                },
            ],
        })
        assert isinstance(c, GroupCondition)
        assert c.op == "all"
        assert len(c.children) == 2
        assert isinstance(c.children[1], GroupCondition)
        assert c.children[1].op == "any"

    def test_invalid(self):
        with pytest.raises(ValueError):
            _parse_condition_tree("invalid")


class TestLoadRules:
    def test_load_empty_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("rules: []\n")
            p = Path(f.name)
        try:
            rules = load_rules(user_path=p, include_defaults=False)
            assert rules == []
        finally:
            p.unlink()

    def test_load_single_rule(self):
        data = {
            "rules": [{
                "name": "test_rule",
                "priority": 100,
                "when": {"message_length": {"gte": 50}},
                "then": "delegate(researcher)",
                "reason": "测试规则",
            }],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            p = Path(f.name)
        try:
            rules = load_rules(user_path=p, include_defaults=False)
            assert len(rules) == 1
            assert rules[0].name == "test_rule"
            assert rules[0].priority == 100
            assert rules[0].then == "delegate(researcher)"
        finally:
            p.unlink()

    def test_priority_ordering(self):
        data = {
            "rules": [
                {"name": "low", "priority": 10, "then": "direct"},
                {"name": "high", "priority": 100, "then": "block"},
                {"name": "mid", "priority": 50, "then": "direct"},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            p = Path(f.name)
        try:
            rules = load_rules(user_path=p, include_defaults=False)
            assert rules[0].name == "high"
            assert rules[1].name == "mid"
            assert rules[2].name == "low"
        finally:
            p.unlink()

    def test_rule_name_dedup(self):
        """同名规则，后加载的覆盖先加载的。"""
        data = {
            "rules": [
                {"name": "dup_rule", "priority": 10, "then": "direct"},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            p = Path(f.name)
        try:
            # 默认规则已有一个 "short_reply_skip"，用户规则同名应覆盖
            rules = load_rules(user_path=p, include_defaults=False)
            # 只有用户规则
            assert len(rules) == 1
            assert rules[0].name == "dup_rule"
        finally:
            p.unlink()

    def test_skip_bad_rule(self):
        """解析失败的规则被跳过。"""
        data = {
            "rules": [
                {"name": "good", "priority": 100, "when": {"message_length": {"lt": 20}}, "then": "direct"},
                {"name": "bad_action", "priority": 50, "then": "invalid_action()"},
                {"name": "", "priority": 50, "then": "direct"},  # 空 name → 跳过
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            p = Path(f.name)
        try:
            rules = load_rules(user_path=p, include_defaults=False)
            names = [r.name for r in rules]
            assert "good" in names
            assert "bad_action" not in names
            assert len(rules) == 1  # 只有 good 被加载
        finally:
            p.unlink()


# ── Engine 集成测试 ──


class TestRuleEngine:
    def test_evaluate_direct_short(self):
        """短消息 → direct"""
        engine = RuleEngine(include_defaults=True)
        decision = engine.evaluate("你好")
        assert decision.action == "direct"

    def test_evaluate_direct_thanks(self):
        """致谢消息 → direct"""
        engine = RuleEngine(include_defaults=True)
        decision = engine.evaluate("谢谢")
        assert decision.action == "direct"

    def test_evaluate_research_trigger(self):
        """较长搜索意图 → delegate(researcher)（绕过 single_question_skip）"""
        engine = RuleEngine(include_defaults=True)
        decision = engine.evaluate("帮我搜索一下最新的 AI agent 框架和它们的对比分析")
        assert decision.action == "delegate"
        assert decision.target == "researcher"

    def test_evaluate_coding_trigger(self):
        """较长编程意图 → delegate(coder)（绕过 single_question_skip）"""
        engine = RuleEngine(include_defaults=True)
        decision = engine.evaluate("帮我写一个 Python 脚本，读取 CSV 文件并进行数据分析")
        assert decision.action == "delegate"
        assert decision.target == "coder"

    def test_evaluate_llm_router_long_message(self):
        """长消息触发 LLM Router"""
        engine = RuleEngine(include_defaults=True)
        long_text = "我需要你帮我做一件事，" + "它涉及很多方面。" * 50
        decision = engine.evaluate(long_text)
        assert decision.action == "llm_router"

    def test_custom_rule_overrides_default(self):
        """自定义规则可以覆盖默认规则。"""
        data = {
            "rules": [
                {
                    "name": "research_trigger",
                    "priority": 200,
                    "when": {"intent_keyword": {"in": ["research"]}},
                    "then": "block",
                    "reason": "测试覆盖",
                },
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            p = Path(f.name)
        try:
            engine = RuleEngine(user_rules_path=p, include_defaults=True)
            decision = engine.evaluate("帮我搜索一下最新的 AI agent 框架和它们的对比分析")
            assert decision.action == "block", f"got {decision.action}"
            assert decision.matched_rule == "research_trigger"
        finally:
            p.unlink()

    def test_extra_facts(self):
        """外部注入事实。"""
        engine = RuleEngine(include_defaults=True)
        # 注入一个不存在的 fact 应该不会影响现有判断
        decision = engine.evaluate("你好", extra_facts={"custom_flag": True})
        assert decision.action == "direct"

    def test_stats(self):
        """引擎统计信息。"""
        engine = RuleEngine(include_defaults=True)
        engine.evaluate("你好")
        engine.evaluate("帮我搜索")
        stats = engine.stats
        assert stats["evaluations"] == 2
        assert "low_cost_hits" in stats

    def test_summary(self):
        """摘要输出。"""
        engine = RuleEngine(include_defaults=True)
        engine.evaluate("你好")
        summary = engine.summary()
        assert "RuleEngine" in summary
        assert "1 次评估" in summary

    def test_empty_engine(self):
        """没有规则时总是返回 direct。"""
        engine = RuleEngine(include_defaults=False)
        decision = engine.evaluate("帮我搜索一下")
        assert decision.action == "direct"

    def test_write_default_config(self):
        """生成默认配置文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing.yaml"
            result = write_default_config(path)
            assert path.exists()
            assert "已写入" in result
            # 再次写入应提示已存在
            result2 = write_default_config(path)
            assert "已存在" in result2


# ── FactRegistry 测试 ──


class TestFactRegistry:
    def test_register_and_get(self):
        assert FactRegistry.get("message_length") is not None
        assert FactRegistry.get("nonexistent") is None

    def test_list_by_cost(self):
        low_facts = FactRegistry.list(cost="low")
        assert all(f.cost == "low" for f in low_facts)
        assert len(low_facts) > 0

    def test_names(self):
        names = FactRegistry.names()
        assert "message_length" in names
        assert "has_code_block" in names
        assert "intent_keyword" in names
