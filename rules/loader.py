"""规则 YAML 加载器 — 读取 routing.yaml 并解析为 Rule 对象。

支持从多个来源加载规则：
  1. 默认规则（内置 defaults.yaml）
  2. 用户规则（~/.chips/routing.yaml）
  3. 项目本地规则（<cwd>/.chips/routing.yaml）

合并策略：同名规则按优先级覆盖，不同名规则合并。
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any, Union

import yaml

from rules.models import GroupCondition, LeafCondition, Rule, parse_action

logger = logging.getLogger("chips.rules.loader")

_DEFAULT_RULES_PATH = Path(__file__).parent / "defaults.yaml"
_USER_RULES_PATH = Path.home() / ".chips" / "routing.yaml"
_LOCAL_RULES_PATH = Path.cwd() / ".chips" / "routing.yaml"


def _parse_condition_tree(
    node: Any,
) -> Union[LeafCondition, GroupCondition]:
    """将 YAML 条件树解析为 Condition 对象树。

    格式示例::

        # 叶子条件
        message_length: { lt: 20 }

        # AND
        all:
          - message_length: { gte: 20 }
          - has_code_block: { eq: true }

        # OR
        any:
          - intent: { in: ["搜索", "调研"] }
          - message_length: { gte: 50 }

        # NOT
        not:
          intent: { in: ["打招呼"] }
    """
    if not isinstance(node, dict):
        msg = f"条件节点必须为 dict，收到 {type(node).__name__}"
        raise ValueError(msg)

    # 检测是否是一个逻辑操作符节点
    if "all" in node and len(node) == 1:
        children = [_parse_condition_tree(item) for item in node["all"]]
        return GroupCondition(op="all", children=children)

    if "any" in node and len(node) == 1:
        children = [_parse_condition_tree(item) for item in node["any"]]
        return GroupCondition(op="any", children=children)

    if "not" in node and len(node) == 1:
        children = [_parse_condition_tree(node["not"])]
        return GroupCondition(op="not", children=children)

    # 叶子条件：{field: {op: value}}
    for field, op_value in node.items():
        if isinstance(op_value, dict):
            for op, value in op_value.items():
                return LeafCondition(field=field, op=op, value=value)

    msg = f"无法解析条件节点: {node}"
    raise ValueError(msg)


def _load_rules_from_yaml(path: Path) -> list[Rule]:
    """从单个 YAML 文件加载规则列表。"""
    if not path.exists():
        logger.debug("rules_file_not_found path=%s", path)
        return []

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    rules_data = data.get("rules", [])
    if not isinstance(rules_data, list):
        logger.warning("rules_format_error path=%s: 'rules' 必须为列表", path)
        return []

    rules: list[Rule] = []
    for i, entry in enumerate(rules_data):
        try:
            rule = _parse_rule(entry)
            if rule is not None:
                rules.append(rule)
        except (ValueError, KeyError, TypeError) as e:
            logger.warning("rule_parse_error path=%s index=%d error=%s", path, i, e)

    return rules


def _parse_rule(entry: dict) -> Rule | None:
    """将单条 YAML 规则条目解析为 Rule 对象。"""
    name = entry.get("name", "").strip()
    if not name:
        logger.warning("rule_missing_name entry=%s", entry)
        return None

    priority = entry.get("priority", 100)
    then_str = entry.get("then", "direct")
    reason = entry.get("reason", "")

    try:
        parse_action(then_str)
    except ValueError as e:
        logger.warning("rule_invalid_action name=%s error=%s", name, e)
        return None

    condition = None
    when = entry.get("when")
    if when is not None:
        try:
            condition = _parse_condition_tree(when)
        except ValueError as e:
            logger.warning("rule_condition_parse_error name=%s error=%s", name, e)
            return None

    tests = entry.get("tests")

    return Rule(
        name=name,
        priority=priority,
        condition=condition,
        then=then_str,
        reason=reason,
        tests=tests,
    )


def load_rules(
    user_path: Path | None = None,
    local_path: Path | None = None,
    include_defaults: bool = True,
) -> list[Rule]:
    """加载所有规则，按优先级合并。

    合并策略：
      1. 默认规则（优先级最低）
      2. 项目本地规则（覆盖同名默认规则）
      3. 用户规则（覆盖同名项目规则）

    同名（name 相同）的规则以高优先级源的为准。
    """
    all_rules: list[Rule] = []

    if include_defaults:
        all_rules.extend(_load_rules_from_yaml(_DEFAULT_RULES_PATH))

    all_rules.extend(_load_rules_from_yaml(local_path or _LOCAL_RULES_PATH))
    all_rules.extend(_load_rules_from_yaml(user_path or _USER_RULES_PATH))

    # 同名去重：保留最后一个（即最上层源）
    seen: dict[str, Rule] = {}
    for rule in all_rules:
        seen[rule.name] = rule
    merged = list(seen.values())

    # 按优先级降序排列
    merged.sort(key=lambda r: r.priority, reverse=True)
    return merged


def write_default_config(path: Path | None = None) -> str:
    """将默认规则写入指定路径（供 'chips router init' 命令使用）。"""
    dest = path or _USER_RULES_PATH
    if dest.exists():
        return f"文件已存在: {dest}"

    dest.parent.mkdir(parents=True, exist_ok=True)

    if _DEFAULT_RULES_PATH.exists():
        with open(_DEFAULT_RULES_PATH, encoding="utf-8") as f:
            content = f.read()
    else:
        content = DEFAULT_RULES_TEXT

    with open(dest, "w", encoding="utf-8") as f:
        f.write(content)

    return f"已写入: {dest}"


# 兜底默认规则文本（当 default.yaml 不存在时使用）
DEFAULT_RULES_TEXT = """# chips Agent 路由规则
#
# 规则按 priority 降序匹配，第一条命中的规则生效。
# 如果所有规则均未命中，走默认行为（direct — 由主 Agent 直接处理）。

rules:
  # ── 安全类规则（高优先级） ──

  - name: "block_emergency_stop"
    priority: 5000
    when:
      intent_keyword: { in: ["紧急停止", "emergency_stop"] }
    then: block
    reason: "用户请求紧急停止"

  # ── 效率类规则（中优先级） ──

  - name: "short_reply_skip"
    priority: 100
    when:
      is_short_reply: { eq: true }
    then: direct
    reason: "短回复，直接处理，无需路由"

  # ── 能力类规则（中优先级） ──

  - name: "research_trigger"
    priority: 80
    when:
      intent_keyword: { in: ["research"] }
    then: delegate(researcher)
    reason: "检测到搜索/调研意图，委派给 Researcher Agent"

  # ── 兜底规则（低优先级） ──

  - name: "long_message_llm_router"
    priority: 10
    when:
      message_length: { gte: 200 }
    then: llm_router
    reason: "长消息，用 LLM Router 做深度分析"
"""
