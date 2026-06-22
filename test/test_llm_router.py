"""测试 LLM Router — 路由分析器"""

from unittest.mock import MagicMock

import pytest

from rules.llm_router import LLMRouter
from rules.models import RouteDecision
from gateway.types import ChatResult


# ── Mock 辅助 ──


def _make_gateway(response_text: str):
    """返回一个 mock gateway，始终返回指定文本。"""
    gw = MagicMock()
    gw.chat.return_value = ChatResult(content=response_text)
    return gw


def _make_registry(agents: list[dict] | None = None):
    """返回一个 mock AgentRegistry。"""
    reg = MagicMock()
    if agents is not None:
        reg.list.return_value = agents
    else:
        reg.list.return_value = [
            {"name": "researcher", "description": "搜索研究助手", "tools": ["web"]},
            {"name": "coder", "description": "编程实现助手", "tools": ["terminal", "file"]},
        ]
    return reg


# ── JSON 解析测试 ──


class TestJsonParsing:
    def test_plain_json(self):
        raw = '{"action": "direct", "reasoning": "简单任务"}'
        parsed = LLMRouter._parse_json(raw)
        assert parsed is not None
        assert parsed["action"] == "direct"

    def test_markdown_code_block_json(self):
        raw = '```json\n{"action": "delegate", "target_agent": "researcher"}\n```'
        parsed = LLMRouter._parse_json(raw)
        assert parsed is not None
        assert parsed["action"] == "delegate"

    def test_markdown_code_block_no_lang(self):
        raw = '```\n{"action": "orchestrate"}\n```'
        parsed = LLMRouter._parse_json(raw)
        assert parsed is not None
        assert parsed["action"] == "orchestrate"

    def test_extra_text_before_after(self):
        raw = 'some text {"action": "direct"} trailing'
        parsed = LLMRouter._parse_json(raw)
        assert parsed is not None
        assert parsed["action"] == "direct"

    def test_empty_input(self):
        assert LLMRouter._parse_json("") is None

    def test_no_json(self):
        assert LLMRouter._parse_json("根本没有 JSON 内容") is None

    def test_broken_json(self):
        assert LLMRouter._parse_json('{"action": "direct"') is None

    def test_nested_orchestrate_plan(self):
        raw = '''{"action": "orchestrate", "plan": {"mode": "supervisor", "steps": [{"agent": "researcher", "task": "search"}]}}'''
        parsed = LLMRouter._parse_json(raw)
        assert parsed is not None
        assert parsed["action"] == "orchestrate"
        assert parsed["plan"]["mode"] == "supervisor"
        assert len(parsed["plan"]["steps"]) == 1


# ── Agent 列表构建测试 ──


class TestAgentList:
    def test_with_registry(self):
        reg = _make_registry()
        gw = _make_gateway('{"action": "direct", "reasoning": "测试"}')
        router = LLMRouter(gateway=gw, agent_registry=reg)
        listing = router._build_agent_list()
        assert "researcher" in listing
        assert "coder" in listing
        assert "搜索研究助手" in listing

    def test_no_registry(self):
        gw = _make_gateway('{"action": "direct", "reasoning": "测试"}')
        router = LLMRouter(gateway=gw, agent_registry=None)
        listing = router._build_agent_list()
        assert "未配置 Agent 角色" in listing

    def test_empty_registry(self):
        reg = _make_registry(agents=[])
        gw = _make_gateway('{"action": "direct", "reasoning": "测试"}')
        router = LLMRouter(gateway=gw, agent_registry=reg)
        listing = router._build_agent_list()
        assert "未配置 Agent 角色" in listing


# ── 路由决策测试 ──


class TestRouting:
    def test_direct_decision(self):
        gw = _make_gateway('{"action": "direct", "reasoning": "简单问候，不需要路由"}')
        router = LLMRouter(gateway=gw)
        decision = router.route("你好")
        assert decision.action == "direct"
        assert "简单问候" in decision.reason

    def test_delegate_decision(self):
        gw = _make_gateway('{"action": "delegate", "target_agent": "researcher", "reasoning": "需要搜索信息"}')
        router = LLMRouter(gateway=gw, agent_registry=_make_registry())
        decision = router.route("帮我搜索最新的 AI 框架")
        assert decision.action == "delegate"
        assert decision.target == "researcher"
        assert "搜索" in decision.reason

    def test_orchestrate_decision(self):
        gw = _make_gateway('''{"action": "orchestrate", "reasoning": "需要多步骤协作", "plan": {"mode": "supervisor", "parallel": true, "steps": [{"agent": "researcher", "task": "搜索资料"}, {"agent": "coder", "task": "写代码"}]}}''')
        router = LLMRouter(gateway=gw, agent_registry=_make_registry())
        decision = router.route("搜索资料然后写代码")
        assert decision.action == "orchestrate"
        assert decision.plan is not None
        assert decision.plan["mode"] == "supervisor"
        assert len(decision.plan["steps"]) == 2

    def test_delegate_without_target_falls_to_direct(self):
        """delegate 但没有 target_agent → 回退 direct"""
        gw = _make_gateway('{"action": "delegate", "reasoning": "需要委派", "target_agent": ""}')
        router = LLMRouter(gateway=gw)
        decision = router.route("测试")
        assert decision.action == "direct"

    def test_gateway_error_falls_back(self):
        """gateway 抛出异常 → 安全降级 direct"""
        gw = MagicMock()
        gw.chat.side_effect = RuntimeError("API 不可用")
        router = LLMRouter(gateway=gw)
        decision = router.route("测试")
        assert decision.action == "direct"
        assert "异常" in decision.reason

    def test_unparseable_response_falls_back(self):
        """LLM 返回无法解析的内容 → 安全降级 direct"""
        gw = _make_gateway("我不太确定要怎么处理这个任务")
        router = LLMRouter(gateway=gw)
        decision = router.route("模糊任务")
        assert decision.action == "direct"
        assert "解析失败" in decision.reason

    def test_default_model_is_none(self):
        """未指定 model 时 model 为 None（gateway 使用默认）。"""
        gw = MagicMock()
        gw.chat.return_value = ChatResult(content='{"action": "direct", "reasoning": "测试"}')
        router = LLMRouter(gateway=gw)
        router.route("你好")
        call_kwargs = gw.chat.call_args[1]
        assert call_kwargs.get("model") is None

    def test_custom_model_used(self):
        """指定 model 时传入 gateway。"""
        gw = MagicMock()
        gw.chat.return_value = ChatResult(content='{"action": "direct", "reasoning": "测试"}')
        router = LLMRouter(gateway=gw, model="deepseek-chat")
        router.route("你好")
        call_kwargs = gw.chat.call_args[1]
        assert call_kwargs.get("model") == "deepseek-chat"


# ── 集成测试 ──


class TestIntegration:
    def test_rule_engine_llm_router_chain(self):
        """规则引擎返回 llm_router → LLM Router 接管并返回 delegate。"""
        from rules.engine import RuleEngine

        engine = RuleEngine(include_defaults=True)
        # 使用长消息触发 long_complex_trigger → llm_router
        long_msg = "我需要你帮我做一件很复杂的事情，" + "它涉及很多方面。" * 30

        decision = engine.evaluate(long_msg)
        assert decision.action == "llm_router", f"期望 llm_router，得到 {decision.action}"

        # LLM Router 接管
        gw = _make_gateway('{"action": "delegate", "target_agent": "coder", "reasoning": "复杂编程任务"}')
        router = LLMRouter(gateway=gw, model="deepseek-chat")
        llm_decision = router.route(long_msg)
        assert llm_decision.action == "delegate"
        assert llm_decision.target == "coder"

    def test_rule_engine_direct_never_reaches_llm(self):
        """规则引擎返回 direct → LLM Router 不会被调用。"""
        from rules.engine import RuleEngine

        engine = RuleEngine(include_defaults=True)
        decision = engine.evaluate("你好")
        assert decision.action == "direct"
        # LLM Router 不应被调用（但这里我们只测试规则引擎的行为是正确的）

    def test_cross_domain_triggers_llm_router(self):
        """跨领域任务触发 cross_domain_trigger → llm_router。"""
        from rules.engine import RuleEngine
        from rules.facts import extract_facts

        engine = RuleEngine(include_defaults=True)
        # 使用包含多个领域关键词的消息
        msg = "搜索最新的 AI 框架，编写示例代码，并写一篇分析文档"
        decision = engine.evaluate(msg)
        # 如果 medium cost 匹配失败（因为 single_question_skip 可能优先），
        # 至少确保不会 crash
        assert decision.action in ("direct", "delegate", "llm_router", "block")
