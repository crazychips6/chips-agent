"""SemanticRouter — 语义路由器

同时完成意图分类和工具推荐：
  1. 用户输入 → embedding
  2. 意图库 → embedding (启动时预计算)
  3. 工具库 → embedding (启动时预计算)
  4. 用户 embedding × 意图 embedding → top-K 意图
  5. 用户 embedding × 工具 embedding → top-K 工具
  6. 融合意图权重 + 查询权重 → 最终工具集

使用方式：
  from agent.semantic_router import semantic_router
  result = semantic_router.route("今天天气怎么样")
  # result = {
  #   "intent": "web_search",
  #   "confidence": 0.85,
  #   "channel": "large",
  #   "tools": ["web", "fetch"],
  #   "top_intents": [("web_search", 0.85), ...],
  #   "top_tools": [("web", 0.92), ...],
  # }
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("chips.agent.semantic_router")


# ── 数据结构 ──


@dataclass
class SemanticEntry:
    """语义索引条目。"""
    name: str
    text: str
    embedding: list[float]
    metadata: dict = field(default_factory=dict)


@dataclass
class RouteResult:
    """路由结果。"""
    intent: str
    confidence: float
    channel: str
    tools: list[str]
    top_intents: list[tuple[str, float]]
    top_tools: list[tuple[str, float]]


# ── 向量运算 ──


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── 语义路由器 ──


class SemanticRouter:
    """语义路由器 — 同时完成意图分类和工具推荐。"""

    def __init__(self):
        self._intent_index: dict[str, SemanticEntry] = {}
        self._tool_index: dict[str, SemanticEntry] = {}
        self._embedding_client = None
        self._built = False

    def _ensure_embedding(self):
        """确保 embedding 客户端可用。"""
        if self._embedding_client is not None:
            return
        from agent.embedding import get_embedding
        self._embedding_client = get_embedding()

    def _build_index(self):
        """构建索引。"""
        if self._built:
            return
        self._ensure_embedding()

        # 构建意图索引
        self._build_intent_index()
        # 构建工具索引
        self._build_tool_index()

        self._built = True

    def _build_intent_index(self):
        """构建意图语义索引。"""
        from agent.intent_loader import intent_registry

        self._intent_index.clear()
        intents = intent_registry.get_all()

        names = []
        texts = []
        for name, intent in intents.items():
            text = self._build_intent_text(intent)
            if not text:
                continue
            names.append(name)
            texts.append(text)
            self._intent_index[name] = SemanticEntry(
                name=name,
                text=text,
                embedding=[],  # 稍后填充
                metadata={
                    "tools": intent.tools,
                    "tool_priorities": intent.tool_priorities,
                    "model": intent.model,
                    "description": intent.description,
                }
            )

        if not texts:
            return

        # 批量获取 embedding
        embeddings = self._embedding_client.embed(texts)
        for name, emb in zip(names, embeddings):
            if emb:
                self._intent_index[name].embedding = emb

        logger.info("intent_index_built count=%d backend=%s",
                     len(self._intent_index), self._embedding_client.name)

    def _build_tool_index(self):
        """构建工具语义索引。"""
        from tool.semantic_descriptions import get_all_tool_descriptions

        self._tool_index.clear()
        descriptions = get_all_tool_descriptions()

        names = []
        texts = []
        for tool_name, desc in descriptions.items():
            text = f"{tool_name}: {desc['description']} {' '.join(desc.get('keywords', []))}"
            names.append(tool_name)
            texts.append(text)
            self._tool_index[tool_name] = SemanticEntry(
                name=tool_name,
                text=text,
                embedding=[],  # 稍后填充
                metadata={"description": desc["description"]}
            )

        if not texts:
            return

        # 批量获取 embedding
        embeddings = self._embedding_client.embed(texts)
        for name, emb in zip(names, embeddings):
            if emb:
                self._tool_index[name].embedding = emb

        logger.info("tool_index_built count=%d", len(self._tool_index))

    def _build_intent_text(self, intent) -> str:
        """构建意图的语义描述文本。"""
        parts = []
        if intent.description:
            parts.append(intent.description)
        if intent.keywords:
            parts.append("关键词: " + ", ".join(intent.keywords))
        return " ".join(parts) if parts else intent.name

    def reload(self):
        """热重载索引。"""
        self._built = False
        self._intent_index.clear()
        self._tool_index.clear()
        self._build_index()

    def route(
        self,
        user_message: str,
        top_k_intents: int = 3,
        top_k_tools: int = 5,
        min_tool_score: float = 0.15,
    ) -> dict[str, Any]:
        """语义路由：同时返回意图和工具推荐。"""
        self._build_index()

        # 获取用户输入的 embedding
        query_emb = self._embedding_client.embed([user_message])
        if not query_emb:
            return self._fallback_route(user_message)
        query_vec = query_emb[0]

        # 1. 意图匹配
        intent_scores = []
        for name, entry in self._intent_index.items():
            if not entry.embedding:
                continue
            sim = _cosine_similarity(query_vec, entry.embedding)
            intent_scores.append((name, sim, entry.metadata))
        intent_scores.sort(key=lambda x: x[1], reverse=True)
        top_intents = intent_scores[:top_k_intents]

        # 2. 工具匹配（独立于意图）
        tool_scores = []
        for name, entry in self._tool_index.items():
            if not entry.embedding:
                continue
            sim = _cosine_similarity(query_vec, entry.embedding)
            tool_scores.append((name, sim))
        tool_scores.sort(key=lambda x: x[1], reverse=True)
        top_tools_from_query = tool_scores[:top_k_tools]

        # 3. 融合：意图推荐的工具 + 查询直接匹配的工具
        fused_tools = self._fuse_tools(
            top_intents,
            top_tools_from_query,
            min_score=min_tool_score,
        )

        # 4. 决定走哪个模型通道
        best_intent = top_intents[0] if top_intents else None
        channel = best_intent[2].get("model", "large") if best_intent else "large"

        result = {
            "intent": best_intent[0] if best_intent else "other",
            "confidence": best_intent[1] if best_intent else 0,
            "channel": channel,
            "tools": fused_tools,
            "top_intents": [(name, score) for name, score, _ in top_intents],
            "top_tools": [(name, score) for name, score in top_tools_from_query],
        }

        logger.info("semantic_route msg=%s intent=%s(%.2f) tools=%s",
                     user_message[:30], result["intent"], result["confidence"],
                     result["tools"][:3])

        return result

    def _fuse_tools(
        self,
        top_intents: list,
        top_tools: list,
        min_score: float = 0.15,
        intent_weight: float = 0.6,
        query_weight: float = 0.4,
    ) -> list[str]:
        """融合意图推荐工具和查询匹配工具。"""
        tool_scores: dict[str, float] = {}

        # 从意图获取工具（权重 0.6）
        for intent_name, intent_score, metadata in top_intents:
            intent_tools = metadata.get("tools", [])
            priorities = metadata.get("tool_priorities", {})
            if isinstance(intent_tools, str) and intent_tools == "all":
                # "all" 不做特殊处理，让查询匹配决定
                continue
            if not isinstance(intent_tools, list):
                continue
            for tool in intent_tools:
                priority = priorities.get(tool, 0.5)
                score = intent_score * intent_weight * priority
                tool_scores[tool] = max(tool_scores.get(tool, 0), score)

        # 从查询直接匹配获取工具（权重 0.4）
        for tool_name, tool_score in top_tools:
            score = tool_score * query_weight
            tool_scores[tool_name] = max(tool_scores.get(tool_name, 0), score)

        # 按分数排序
        sorted_tools = sorted(tool_scores.items(), key=lambda x: x[1], reverse=True)

        # 过滤低分工具
        return [tool for tool, score in sorted_tools if score >= min_score]

    def _fallback_route(self, user_message: str) -> dict[str, Any]:
        """fallback 路由（embedding 不可用时）。"""
        return {
            "intent": "other",
            "confidence": 0,
            "channel": "large",
            "tools": [],
            "top_intents": [],
            "top_tools": [],
        }


# 模块级单例
semantic_router = SemanticRouter()
