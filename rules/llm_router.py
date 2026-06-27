"""LLM Router — 用一次轻量 LLM 调用分析任务，输出路由决策

在规则引擎（低代价）匹配失败后触发，处理模糊/复杂/跨领域任务。

调用流程::

    LLMRouter.route(task)
      ├─ 构造 prompt（任务描述 + 可用 Agent 列表）
      ├─ 调用 LLM（同主 Agent 模型，max_tokens=500）
      ├─ 解析 JSON 输出
      └─ 返回 RouteDecision
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from rules.models import RouteDecision

logger = logging.getLogger("chips.rules.llm_router")

_ROUTER_SYSTEM_PROMPT = """你是一个任务分析器，判断用户任务是否需要路由到 Specialist Agent。

当前可用 Agent 角色：
{agent_list}

判断标准：
1. **direct** — 简单任务（问候、单步操作、信息查询），直接由主 Agent 处理
2. **delegate** — 任务有明确的专业方向，适合单个 Specialist Agent 独立完成
3. **orchestrate** — 跨领域/多步骤/需要多角色协作的复杂任务

输出 JSON（不要输出其他内容，不要用 markdown 代码块）：

{{
    "needs_routing": true,
    "action": "direct",
    "target_agent": "",
    "reasoning": "简要判断理由",
    "plan": null
}}

delegate 示例输出：
{{"needs_routing": true, "action": "delegate", "target_agent": "researcher", "reasoning": "用户需要搜索信息", "plan": null}}

orchestrate 示例输出：
{{"needs_routing": true, "action": "orchestrate", "target_agent": "", "reasoning": "需要搜索和编码", "plan": {{"mode": "supervisor", "parallel": true, "steps": [{{"agent": "researcher", "task": "搜索xxx"}}, {{"agent": "coder", "task": "编写xxx"}}]}}}}"""


class LLMRouter:
    """LLM 路由分析器。

    Args:
        gateway: ModelGateway 实例（用于 LLM 调用）
        agent_registry: AgentRegistry 或类似接口，提供可用 Agent 列表
        model: 可选，指定使用的模型名。默认 None 表示使用主 Agent 的模型
    """

    def __init__(
        self,
        gateway: Any,
        agent_registry: Any = None,
        model: str | None = None,
    ):
        self._gateway = gateway
        self._registry = agent_registry
        self._model = model

    def route(self, task: str) -> RouteDecision:
        """分析任务并返回路由决策。

        失败时返回 direct（安全降级）。
        """
        try:
            agent_list = self._build_agent_list()
            prompt = _ROUTER_SYSTEM_PROMPT.format(agent_list=agent_list)

            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"分析任务: {task}"},
            ]

            result = self._gateway.chat(
                messages=messages,
                model=self._model,
                max_tokens=500,
                temperature=0.1,
            )

            raw = result.content or ""
            parsed = self._parse_json(raw)

            if parsed is None:
                logger.warning("llm_router_parse_failed raw=%s", raw[:200])
                return RouteDecision(action="direct", reason="LLM Router 解析失败，走默认行为")

            action = parsed.get("action", "direct")
            target = parsed.get("target_agent", "")
            reasoning = parsed.get("reasoning", "")
            plan = parsed.get("plan")

            if action == "orchestrate" and plan:
                return RouteDecision(
                    action="orchestrate",
                    target=target,
                    reason=f"LLM Router: {reasoning}",
                    confidence=0.7,
                    plan=plan,
                )

            if action == "delegate" and target:
                return RouteDecision(
                    action="delegate",
                    target=target,
                    reason=f"LLM Router: {reasoning}",
                    confidence=0.7,
                )

            return RouteDecision(
                action="direct",
                reason=f"LLM Router: {reasoning}",
            )

        except Exception:
            logger.exception("llm_router_error task=%s", task[:100])
            return RouteDecision(action="direct", reason="LLM Router 异常，走默认行为")

    def _build_agent_list(self) -> str:
        """构建可用 Agent 角色列表文本。"""
        if self._registry is None:
            return "  - (未配置 Agent 角色，所有任务由主 Agent 处理)"

        try:
            agents = self._registry.list()
            if not agents:
                return "  - (未配置 Agent 角色，所有任务由主 Agent 处理)"

            lines = []
            for a in agents:
                name = a.get("name", "?")
                desc = a.get("description", "")
                tools = a.get("tools", [])
                tool_str = ", ".join(tools[:5]) if tools else "默认"
                lines.append(f"  - {name}: {desc} (tools: {tool_str})")
            return "\n".join(lines)
        except Exception:
            return "  - (获取 Agent 列表失败)"

    @staticmethod
    def _parse_json(raw: str) -> dict | None:
        """从 LLM 回复中提取 JSON。

        处理情形：
          - 纯 JSON
          - markdown 代码块 ```json ... ```
          - 前后有多余文本
        """
        # 尝试提取 markdown 代码块
        m = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        if m:
            raw = m.group(1).strip()

        # 找第一个 { 到最后一个 }
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        json_str = raw[start : end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None
