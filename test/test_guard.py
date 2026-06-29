"""测试 GuardEngine — 安全拦截引擎"""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from safety.guard import GuardEngine, _collect_keywords
from safety.loader import load_rules, write_default_config, _parse_condition_tree
from safety.models import (
    GroupCondition,
    LeafCondition,
    RouteDecision,
    parse_action,
)


# ── 关键字收集测试 ──


class TestCollectKeywords:
    def test_collect_in_list(self):
        c = LeafCondition(field="intent_keyword", op="in", value=["紧急停止", "rm -rf /"])
        assert _collect_keywords(c) == ["紧急停止", "rm -rf /"]

    def test_collect_nested_group(self):
        c = GroupCondition(op="all", children=[
            LeafCondition(field="a", op="in", value=["foo"]),
            LeafCondition(field="b", op="in", value=["bar"]),
        ])
        assert "foo" in _collect_keywords(c)
        assert "bar" in _collect_keywords(c)

    def test_empty_for_unknown_op(self):
        c = LeafCondition(field="x", op="exists")
        assert _collect_keywords(c) == []


# ── Models 测试 ──


class TestParseAction:
    def test_direct(self):
        assert parse_action("direct") == "direct"

    def test_block(self):
        assert parse_action("block") == "block"

    def test_invalid_action(self):
        with pytest.raises(ValueError, match="无效的动作格式"):
            parse_action("delegate(researcher)")

    def test_invalid_action_name(self):
        with pytest.raises(ValueError, match="未知动作"):
            parse_action("nonexistent_tool")

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="无效的动作格式"):
            parse_action("")


class TestLeafCondition:
    def test_eq_match(self):
        c = LeafCondition(field="intent_keyword", op="in", value=["紧急停止"])
        assert c.evaluate({"intent_keyword": {"紧急停止", "操作"}})

    def test_eq_not_match(self):
        c = LeafCondition(field="intent_keyword", op="in", value=["rm -rf"])
        assert not c.evaluate({"intent_keyword": {"你好"}})

    def test_in_match(self):
        c = LeafCondition(field="intent", op="in", value=["search", "research"])
        assert c.evaluate({"intent": "research"})

    def test_in_not_match(self):
        c = LeafCondition(field="intent", op="in", value=["search"])
        assert not c.evaluate({"intent": "coding"})

    def test_exists_match(self):
        c = LeafCondition(field="intent", op="exists")
        assert c.evaluate({"intent": ["research"]})

    def test_exists_not_match(self):
        c = LeafCondition(field="missing_field", op="exists")
        assert not c.evaluate({"intent": ["research"]})

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

    def test_not_inverts(self):
        c = GroupCondition(op="not", children=[
            LeafCondition(field="a", op="eq", value=True),
        ])
        assert c.evaluate({"a": False})
        assert not c.evaluate({"a": True})


class TestRouteDecision:
    def test_direct(self):
        d = RouteDecision(action="direct")
        assert not d.is_block()

    def test_block(self):
        d = RouteDecision(action="block")
        assert d.is_block()

    def test_str_direct(self):
        d = RouteDecision(action="direct", reason="放行")
        assert "direct" in str(d)

    def test_str_block(self):
        d = RouteDecision(action="block", reason="危险命令", matched_rule="dangerous")
        assert "block" in str(d)
        assert "dangerous" in str(d)


# ── Loader 测试 ──


class TestConditionParsing:
    def test_simple(self):
        c = _parse_condition_tree({"intent_keyword": {"in": ["紧急停止"]}})
        assert isinstance(c, LeafCondition)
        assert c.field == "intent_keyword"
        assert c.op == "in"

    def test_all_group(self):
        c = _parse_condition_tree({
            "all": [
                {"message_length": {"gte": 20}},
                {"has_code_block": {"eq": True}},
            ],
        })
        assert isinstance(c, GroupCondition)
        assert c.op == "all"

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

    def test_load_block_rule(self):
        data = {
            "rules": [{
                "name": "test_block",
                "priority": 100,
                "when": {"intent_keyword": {"in": ["dangerous"]}},
                "then": "block",
                "reason": "测试拦截",
            }],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            p = Path(f.name)
        try:
            rules = load_rules(user_path=p, include_defaults=False)
            assert len(rules) == 1
            assert rules[0].name == "test_block"
            assert rules[0].then == "block"
        finally:
            p.unlink()

    def test_parse_bad_action_skipped(self):
        """旧版 delegate(xxx) 等不再支持的 action 会被跳过。"""
        data = {
            "rules": [
                {"name": "good", "priority": 100, "then": "block"},
                {"name": "bad", "priority": 50, "then": "delegate(researcher)"},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            p = Path(f.name)
        try:
            rules = load_rules(user_path=p, include_defaults=False)
            names = [r.name for r in rules]
            assert "good" in names
            assert "bad" not in names
        finally:
            p.unlink()


# ── GuardEngine 集成测试 ──


class TestGuardEngine:
    def test_pass_normal_message(self):
        engine = GuardEngine(include_defaults=True)
        decision = engine.evaluate("你好")
        assert decision.action == "direct"

    def test_block_emergency_stop(self):
        engine = GuardEngine(include_defaults=True)
        decision = engine.evaluate("紧急停止")
        assert decision.action == "block"

    def test_block_dangerous_command(self):
        engine = GuardEngine(include_defaults=True)
        decision = engine.evaluate("rm -rf /")
        assert decision.action == "block"

    def test_block_english_keyword(self):
        engine = GuardEngine(include_defaults=True)
        decision = engine.evaluate("emergency_stop now")
        assert decision.action == "block"

    def test_custom_rule_overrides_default(self):
        """自定义规则可以覆盖默认规则。"""
        data = {
            "rules": [
                {
                    "name": "block_emergency_stop",
                    "priority": 200,
                    "when": {"intent_keyword": {"in": ["紧急停止"]}},
                    "then": "direct",
                    "reason": "用户自定义覆盖",
                },
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            p = Path(f.name)
        try:
            engine = GuardEngine(user_rules_path=p, include_defaults=True)
            decision = engine.evaluate("紧急停止")
            assert decision.action == "direct", f"自定义规则应覆盖默认，got {decision.action}"
        finally:
            p.unlink()

    def test_stats(self):
        engine = GuardEngine(include_defaults=True)
        engine.evaluate("你好")
        engine.evaluate("紧急停止")
        stats = engine.stats
        assert stats["evaluations"] == 2
        assert stats["blocks"] == 1

    def test_summary(self):
        engine = GuardEngine(include_defaults=True)
        engine.evaluate("你好")
        summary = engine.summary()
        assert "GuardEngine" in summary

    def test_empty_engine_always_direct(self):
        engine = GuardEngine(include_defaults=False)
        decision = engine.evaluate("紧急停止")
        assert decision.action == "direct"

    def test_write_default_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing.yaml"
            result = write_default_config(path)
            assert path.exists()
            assert "已写入" in result
            result2 = write_default_config(path)
            assert "已存在" in result2

    def test_reload(self):
        engine = GuardEngine(include_defaults=True)
        assert len(engine.rules) > 0
        old_count = len(engine.rules)
        engine.reload()
        assert len(engine.rules) == old_count
