"""语义检索层 — Embedding 向量检索候选意图 Top-K

在规则预过滤之后、LLM 分类之前执行。
用 Embedding 模型向量化用户消息，与意图向量计算余弦相似度。

支持两种后端：
  - API：通过 OpenAI 兼容接口调用 embedding API（DeepSeek/OpenAI/等）
  - fallback：TF-IDF（API 不可用时自动降级）
"""

from __future__ import annotations

import logging
import math
import os
from collections import Counter
from typing import Any

from agent.intent_loader import intent_registry

logger = logging.getLogger("chips.agent.semantic_matcher")


# ── TF-IDF fallback（API 不可用时使用）──


def _tokenize(text: str) -> list[str]:
    """简单分词：按字符切分（中文）+ 按空格切分（英文）。"""
    tokens = []
    current_en = []
    for char in text:
        if char.isascii() and char.isalnum():
            current_en.append(char.lower())
        else:
            if current_en:
                tokens.append("".join(current_en))
                current_en = []
            if char.strip():
                tokens.append(char)
    if current_en:
        tokens.append("".join(current_en))
    return tokens


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf = Counter(tokens)
    total = len(tokens) if tokens else 1
    return {term: (count / total) * idf.get(term, 1.0) for term, count in tf.items()}


def _cosine_sim_dict(v1: dict[str, float], v2: dict[str, float]) -> float:
    if not v1 or not v2:
        return 0.0
    common = set(v1.keys()) & set(v2.keys())
    if not common:
        return 0.0
    dot = sum(v1[k] * v2[k] for k in common)
    norm1 = math.sqrt(sum(v ** 2 for v in v1.values()))
    norm2 = math.sqrt(sum(v ** 2 for v in v2.values()))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def _cosine_sim_vec(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Embedding API 客户端 ──


class EmbeddingClient:
    """Embedding API 客户端 — 通过 OpenAI 兼容接口调用。"""

    def __init__(self):
        self._client = None
        self._model: str = ""
        self._available: bool | None = None

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            from openai import OpenAI
            api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
            base_url = os.getenv("CHIPS_BASE_URL", "https://api.deepseek.com")
            if not api_key:
                return
            self._client = OpenAI(api_key=api_key, base_url=base_url)
            self._model = os.getenv("CHIPS_EMBEDDING_MODEL", "text-embedding-3-small")
        except Exception as e:
            logger.warning("embedding_client_init_failed: %s", e)

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        self._ensure_client()
        if self._client is None:
            self._available = False
            return False
        # 尝试一次简单调用检测可用性
        try:
            resp = self._client.embeddings.create(
                model=self._model,
                input=["test"],
            )
            self._available = len(resp.data) > 0
        except Exception as e:
            logger.info("embedding_api_unavailable: %s", e)
            self._available = False
        return self._available

    def embed(self, texts: list[str]) -> list[list[float]]:
        """获取文本的 embedding 向量。"""
        self._ensure_client()
        if self._client is None:
            return []
        try:
            resp = self._client.embeddings.create(
                model=self._model,
                input=texts,
            )
            return [item.embedding for item in resp.data]
        except Exception as e:
            logger.warning("embedding_api_failed: %s", e)
            return []


# ── 语义匹配器 ──


class SemanticMatcher:
    """语义检索器 — Embedding 向量检索 + TF-IDF fallback。"""

    def __init__(self):
        self._embed_client = EmbeddingClient()
        # Embedding 模式
        self._intent_embeddings: dict[str, list[float]] = {}
        self._intent_texts: dict[str, str] = {}
        # TF-IDF fallback 模式
        self._idf: dict[str, float] = {}
        self._intent_vectors: dict[str, dict[str, float]] = {}
        # 使用模式
        self._use_embedding: bool | None = None

    def _build_index(self):
        """构建索引（自动选择 embedding 或 TF-IDF）。"""
        intents = intent_registry.get_all()

        # 尝试 embedding 模式
        if self._use_embedding is None:
            self._use_embedding = self._embed_client.is_available()
            if self._use_embedding:
                logger.info("semantic_backend=embedding")
            else:
                logger.info("semantic_backend=tfidf (embedding unavailable)")

        if self._use_embedding:
            self._build_embedding_index(intents)
        else:
            self._build_tfidf_index(intents)

    def _build_embedding_index(self, intents: dict):
        """构建 embedding 索引。"""
        self._intent_embeddings.clear()
        self._intent_texts.clear()

        # 收集所有意图的文本
        names = []
        texts = []
        for name, intent in intents.items():
            if not intent.keywords:
                continue
            # 将关键词拼接成文本
            text = " ".join(intent.keywords)
            names.append(name)
            texts.append(text)
            self._intent_texts[name] = text

        if not texts:
            return

        # 批量获取 embedding
        embeddings = self._embed_client.embed(texts)
        for name, emb in zip(names, embeddings):
            self._intent_embeddings[name] = emb

        logger.info("embedding_index_built intents=%d", len(self._intent_embeddings))

    def _build_tfidf_index(self, intents: dict):
        """构建 TF-IDF 索引（fallback）。"""
        docs: dict[str, list[str]] = {}
        for name, intent in intents.items():
            if not intent.keywords:
                continue
            doc_tokens = []
            for kw in intent.keywords:
                doc_tokens.extend(_tokenize(kw))
            doc_tokens.extend(_tokenize(name))
            docs[name] = doc_tokens

        if not docs:
            return

        n_docs = len(docs)
        df: Counter = Counter()
        for tokens in docs.values():
            for term in set(tokens):
                df[term] += 1

        self._idf = {term: math.log((n_docs + 1) / (count + 1)) + 1
                     for term, count in df.items()}
        self._intent_vectors = {
            name: _tfidf_vector(tokens, self._idf)
            for name, tokens in docs.items()
        }

    def reload(self):
        """热重载索引。"""
        self._use_embedding = None
        self._build_index()

    def match(self, text: str, top_k: int = 3, threshold: float = 0.1) -> list[tuple[str, float]]:
        """匹配文本，返回候选意图列表 [(intent_name, score), ...]。"""
        if self._use_embedding is None:
            self._build_index()

        if self._use_embedding and self._intent_embeddings:
            return self._match_embedding(text, top_k, threshold)
        else:
            return self._match_tfidf(text, top_k, threshold)

    def _match_embedding(self, text: str, top_k: int, threshold: float) -> list[tuple[str, float]]:
        """Embedding 匹配。"""
        query_emb = self._embed_client.embed([text])
        if not query_emb:
            return self._match_tfidf(text, top_k, threshold)

        query_vec = query_emb[0]
        scores: list[tuple[str, float]] = []
        for name, intent_emb in self._intent_embeddings.items():
            sim = _cosine_sim_vec(query_vec, intent_emb)
            if sim >= threshold:
                scores.append((name, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _match_tfidf(self, text: str, top_k: int, threshold: float) -> list[tuple[str, float]]:
        """TF-IDF 匹配（fallback）。"""
        if not self._intent_vectors:
            return []

        query_tokens = _tokenize(text)
        if not query_tokens:
            return []

        query_vec = _tfidf_vector(query_tokens, self._idf)
        scores: list[tuple[str, float]] = []
        for name, intent_vec in self._intent_vectors.items():
            sim = _cosine_sim_dict(query_vec, intent_vec)
            if sim >= threshold:
                scores.append((name, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def match_one(self, text: str, threshold: float = 0.5) -> ClassifyResult | None:
        """匹配文本，返回最佳的 ClassifyResult 或 None。"""
        from agent.classify_result import ClassifyResult, PRIORITY_SEMANTIC
        from agent.intent_loader import intent_registry

        candidates = self.match(text, top_k=1, threshold=threshold)
        if not candidates:
            return None

        best_intent, best_score = candidates[0]
        intent_def = intent_registry.get(best_intent)
        predicted_tools = []
        if intent_def and isinstance(intent_def.tools, list):
            predicted_tools = intent_def.tools

        return ClassifyResult(
            intent=best_intent,
            confidence=best_score,
            priority=PRIORITY_SEMANTIC,
            source="semantic",
            predicted_tools=predicted_tools,
            candidates=[{"intent": i, "score": int(s * 100)} for i, s in candidates],
        )


# 模块级单例
semantic_matcher = SemanticMatcher()
