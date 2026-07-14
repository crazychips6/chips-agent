"""语义检索层 — TF-IDF + cosine similarity 候选意图 Top-K

在规则预过滤之后、LLM 分类之前执行。
用 TF-IDF 向量化用户消息，与意图关键词计算相似度，返回候选意图。

零外部依赖（只用 Python 标准库 + 数学计算）。
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Any

from agent.intent_loader import intent_registry

logger = logging.getLogger("chips.agent.semantic_matcher")


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
    """计算 TF-IDF 向量。"""
    tf = Counter(tokens)
    total = len(tokens) if tokens else 1
    return {term: (count / total) * idf.get(term, 1.0) for term, count in tf.items()}


def _cosine_sim(v1: dict[str, float], v2: dict[str, float]) -> float:
    """计算余弦相似度。"""
    if not v1 or not v2:
        return 0.0
    # 交集
    common = set(v1.keys()) & set(v2.keys())
    if not common:
        return 0.0
    dot = sum(v1[k] * v2[k] for k in common)
    norm1 = math.sqrt(sum(v ** 2 for v in v1.values()))
    norm2 = math.sqrt(sum(v ** 2 for v in v2.values()))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


class SemanticMatcher:
    """语义检索器 — 基于 TF-IDF 的意图候选匹配。"""

    def __init__(self):
        self._idf: dict[str, float] = {}
        self._intent_vectors: dict[str, dict[str, float]] = {}
        self._build_index()

    def _build_index(self):
        """构建 TF-IDF 索引。"""
        intents = intent_registry.get_all()

        # 收集所有文档（每个意图的关键词作为一个文档）
        docs: dict[str, list[str]] = {}
        for name, intent in intents.items():
            if not intent.keywords:
                continue
            # 将关键词拼接成文档
            doc_tokens = []
            for kw in intent.keywords:
                doc_tokens.extend(_tokenize(kw))
            # 额外加入意图名本身
            doc_tokens.extend(_tokenize(name))
            docs[name] = doc_tokens

        if not docs:
            return

        # 计算 IDF
        n_docs = len(docs)
        df: Counter = Counter()
        for tokens in docs.values():
            unique_terms = set(tokens)
            for term in unique_terms:
                df[term] += 1

        self._idf = {term: math.log((n_docs + 1) / (count + 1)) + 1
                     for term, count in df.items()}

        # 计算每个意图的 TF-IDF 向量
        self._intent_vectors = {
            name: _tfidf_vector(tokens, self._idf)
            for name, tokens in docs.items()
        }

        logger.info("semantic_index_built intents=%d vocab=%d",
                     len(self._intent_vectors), len(self._idf))

    def reload(self):
        """热重载索引。"""
        self._build_index()

    def match(self, text: str, top_k: int = 3, threshold: float = 0.1) -> list[tuple[str, float]]:
        """匹配文本，返回候选意图列表 [(intent_name, score), ...]。

        按相似度降序排列，只返回超过 threshold 的候选。
        """
        if not self._intent_vectors:
            return []

        # 向量化用户消息
        query_tokens = _tokenize(text)
        if not query_tokens:
            return []

        query_vec = _tfidf_vector(query_tokens, self._idf)

        # 计算与每个意图的相似度
        scores: list[tuple[str, float]] = []
        for name, intent_vec in self._intent_vectors.items():
            sim = _cosine_sim(query_vec, intent_vec)
            if sim >= threshold:
                scores.append((name, sim))

        # 按相似度降序排列
        scores.sort(key=lambda x: x[1], reverse=True)

        return scores[:top_k]


# 模块级单例
semantic_matcher = SemanticMatcher()
