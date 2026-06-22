"""Fact Extractor 注册表 — 从用户输入中提取路由判断所需的事实。

Fact 是路由规则判断的基本单元。每个 Fact 有一个名称、一个代价等级和提取函数。

代价等级决定引擎的调用策略：
  - low: 始终提取，不影响性能
  - medium: 仅在规则需要时提取（延迟加载）
  - high: 仅在低代价 fact 不足以做出决策时提取（如 LLM 调用）
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("chips.rules.facts")


@dataclass
class FactExtractor:
    """单个事实的提取器。"""

    name: str
    cost: str  # "low" | "medium" | "high"
    fn: Callable[[str], Any]
    description: str = ""


class FactRegistry:
    """全局 Fact 提取器注册表。

    用法::

        @FactRegistry.register("message_length", cost="low", description="输入文本长度")
        def extract_length(text: str) -> int:
            return len(text)
    """

    _extractors: dict[str, FactExtractor] = {}

    @classmethod
    def register(
        cls,
        name: str,
        cost: str = "low",
        description: str = "",
    ) -> Callable:
        """装饰器：注册一个 Fact Extractor。"""
        def decorator(fn: Callable[[str], Any]) -> Callable[[str], Any]:
            cls._extractors[name] = FactExtractor(
                name=name,
                cost=cost,
                fn=fn,
                description=description or fn.__doc__ or "",
            )
            return fn
        return decorator

    @classmethod
    def get(cls, name: str) -> FactExtractor | None:
        return cls._extractors.get(name)

    @classmethod
    def list(cls, cost: str | None = None) -> list[FactExtractor]:
        if cost:
            return [e for e in cls._extractors.values() if e.cost == cost]
        return list(cls._extractors.values())

    @classmethod
    def names(cls) -> list[str]:
        return list(cls._extractors.keys())


# ── 内建 Fact Extractor（低代价） ──


@FactRegistry.register("message_length", cost="low", description="用户消息的字符长度")
def _extract_length(text: str) -> int:
    return len(text)


@FactRegistry.register("has_code_block", cost="low", description="消息中是否包含代码块标记 ```")
def _extract_code_block(text: str) -> bool:
    return "```" in text


@FactRegistry.register("question_count", cost="low", description="消息中问号的数量")
def _extract_question_count(text: str) -> int:
    return text.count("?") + text.count("？")


@FactRegistry.register("is_short_reply", cost="low", description="是否可能是简短回复（纯文本、无标点、极短）")
def _extract_is_short_reply(text: str) -> bool:
    text = text.strip()
    if len(text) > 15:
        return False
    # 纯粹的简短回复，如 "好的" "谢谢" "ok"
    no_punct = re.sub(r"[，。！？、；：\"\"''【】《》（）\s.,!?;:\"'\[\]{}()]", "", text)
    return len(no_punct) <= 10


@FactRegistry.register("mentions_file", cost="low", description="消息中是否提及文件操作（读/写/编辑）")
def _extract_mentions_file(text: str) -> bool:
    patterns = [
        r"(读取|阅读|打开|查看|显示|cat|read|open|view)\s*(文件|文档|日志|log|config|\.\w+)",
        r"(写入|保存|编辑|修改|创建|删除|write|save|edit|modify|create|delete)\s*(文件|文档|配置|\.\w+)",
        # 宽松匹配：动词和"文件"之间可以有其他词，如"读取一下这个文件"
        r"(读|取|读一下|打开|查看|cat|view).{0,6}(文件|文档|日志|log)",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


@FactRegistry.register("intent_keyword", cost="low", description="通过关键词匹配的意图列表")
def _extract_intent_keyword(text: str) -> list[str]:
    """使用关键词匹配粗略识别用户意图。"""
    intents: list[str] = []

    # 搜索/调研类
    if re.search(r"(搜索|查一下|调研|研究|research|search|find|look\s*up)", text, re.IGNORECASE):
        intents.append("research")

    # 编程类
    if re.search(r"(写|写代码|编程|实现|开发|bug|调试|代码|code|write|implement|debug|fix)", text, re.IGNORECASE):
        intents.append("coding")

    # 文件操作类
    if re.search(r"(读取|保存|编辑|文件|file|read|write|save|edit)", text, re.IGNORECASE):
        intents.append("file_operation")

    # 信息查询类
    if re.search(r"(什么|是什么|怎么|为什么|how|what|why|tell me about|explain)", text, re.IGNORECASE):
        intents.append("information")

    # 分析/审查类
    if re.search(r"(分析|审查|审计|review|analyze|audit|check|validate)", text, re.IGNORECASE):
        intents.append("review")

    return intents


# ── 内建 Fact Extractor（中代价） ──


@FactRegistry.register("mentions_tool", cost="medium", description="消息中提及的已知工具名")
def _extract_mentions_tool(text: str) -> list[str]:
    """检测消息中是否提到已知工具名。

    注意：此函数不持有工具注册表引用，返回的 always 空列表，
    实际使用时应通过 RuleEngine 注入 tool_names 后调用带有上下文的版本。
    """
    return []


@FactRegistry.register("task_complexity_signals", cost="medium", description="任务复杂度信号（多步骤/跨领域/多角色）")
def _extract_complexity_signals(text: str) -> dict[str, bool]:
    """检测任务复杂度的信号。"""
    result: dict[str, bool] = {}

    # 多步骤信号：包含明确的步骤编号或顺序词
    result["has_multi_step"] = bool(
        re.search(
            r"(第一步|第二步|首先|然后|接着|最后|步骤|step\s*\d|1\.\s|2\.\s|3\.\s)",
            text, re.IGNORECASE,
        )
    )

    # 跨领域信号：同时提到多个领域的关键词
    domain_count = 0
    domains = [
        r"(搜索|查|research|search)",
        r"(代码|编程|写|编写|code|implement|develop)",
        r"(测试|test|验证|verify)",
        r"(设计|架构|design|architecture)",
        r"(文档|文档|doc|document)",
        r"(部署|deploy|发布|release)",
    ]
    for d in domains:
        if re.search(d, text, re.IGNORECASE):
            domain_count += 1
    result["cross_domain"] = domain_count >= 2

    # 工具链长度预估：粗略按句子数和动词数估算
    sentences = re.split(r"[。！？.!?\n]", text)
    verbs = re.findall(
        r"(搜索|查|分析|编写|测试|部署|设计|审查|对比|整理|汇总|research|analyze|write|test|deploy|design|review|compare|summarize)",
        text, re.IGNORECASE,
    )
    result["estimated_steps"] = max(len(sentences) // 2, len(verbs) // 2, 1)

    return result


# ── 辅助函数 ──


def extract_facts(
    text: str,
    *,
    fact_names: list[str] | None = None,
    cost: str | None = "low",
    **kwargs: Any,
) -> dict[str, Any]:
    """按需提取事实。

    Args:
        text: 用户输入文本
        fact_names: 指定要提取的 fact 名。为 None 时提取所有匹配 cost 的 fact
        cost: 限制提取的代价等级。None 表示不限
        **kwargs: 传递给 extractor 的额外参数

    Returns:
        {fact_name: fact_value, ...}
    """
    results: dict[str, Any] = {}

    if fact_names:
        extractors = [FactRegistry.get(n) for n in fact_names if FactRegistry.get(n) is not None]
    elif cost:
        extractors = FactRegistry.list(cost=cost)
    else:
        extractors = FactRegistry.list()

    # tool_names 无论是否传参都写入结果（空集也写入，避免 None）
    tool_names: set[str] = kwargs.get("tool_names", set())
    results["tool_names"] = tool_names

    for ext in extractors:
        try:
            results[ext.name] = ext.fn(text)
        except Exception:
            logger.debug("fact_extract_error name=%s", ext.name, exc_info=True)
            results[ext.name] = None

    # mentions_tool 特殊处理：只要有 tool_names 就提取，不受 cost 过滤限制
    text_lower = text.lower()
    if tool_names:
        results["mentions_tool"] = [
            t for t in tool_names
            if t.replace("_", " ") in text_lower or t in text_lower
        ]
    else:
        results["mentions_tool"] = []

    return results
