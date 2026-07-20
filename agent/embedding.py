"""Embedding 模块 — 本地优先，API fallback

HybridEmbedding 自动选择最佳后端：
  1. sentence-transformers (本地，零成本)
  2. EmbeddingClient (API，需网络)

使用方式：
  from agent.embedding import get_embedding
  client = get_embedding()
  vectors = client.embed(["文本1", "文本2"])
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("chips.agent.embedding")


class EmbeddingBackend(ABC):
    """Embedding 后端接口。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """获取文本的 embedding 向量。"""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """检查后端是否可用。"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """后端名称。"""
        ...


class LocalEmbedding(EmbeddingBackend):
    """本地 sentence-transformers embedding。"""

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        self._model_name = model_name
        self._model = None
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
            self._available = True
            logger.info("local_embedding_loaded model=%s", self._model_name)
        except ImportError:
            self._available = False
            logger.info("sentence_transformers_not_installed")
        except Exception as e:
            self._available = False
            logger.warning("local_embedding_init_failed: %s", e)
        return self._available

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.is_available() or self._model is None:
            return []
        try:
            embeddings = self._model.encode(texts, show_progress_bar=False)
            return embeddings.tolist()
        except Exception as e:
            logger.warning("local_embedding_failed: %s", e)
            return []

    @property
    def name(self) -> str:
        return f"local({self._model_name})"


class APIEmbedding(EmbeddingBackend):
    """API embedding (OpenAI 兼容接口)。"""

    def __init__(self):
        self._client = None
        self._model: str = ""
        self._available: bool | None = None

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            import os
            from openai import OpenAI
            api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
            base_url = os.getenv("CHIPS_BASE_URL", "https://api.deepseek.com")
            if not api_key:
                return
            self._client = OpenAI(api_key=api_key, base_url=base_url)
            self._model = os.getenv("CHIPS_EMBEDDING_MODEL", "text-embedding-3-small")
        except Exception as e:
            logger.warning("api_embedding_init_failed: %s", e)

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        self._ensure_client()
        if self._client is None:
            self._available = False
            return False
        try:
            resp = self._client.embeddings.create(
                model=self._model,
                input=["test"],
            )
            self._available = len(resp.data) > 0
        except Exception as e:
            logger.info("api_embedding_unavailable: %s", e)
            self._available = False
        return self._available

    def embed(self, texts: list[str]) -> list[list[float]]:
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
            logger.warning("api_embedding_failed: %s", e)
            return []

    @property
    def name(self) -> str:
        return f"api({self._model})"


class HybridEmbedding(EmbeddingBackend):
    """混合 embedding：本地优先，API fallback。"""

    def __init__(self):
        self._local = LocalEmbedding()
        self._api = APIEmbedding()
        self._active: EmbeddingBackend | None = None

    def _select_backend(self) -> EmbeddingBackend:
        """选择可用的后端。"""
        if self._local.is_available():
            return self._local
        if self._api.is_available():
            return self._api
        raise RuntimeError("No embedding backend available")

    def is_available(self) -> bool:
        try:
            self._select_backend()
            return True
        except RuntimeError:
            return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._active is None:
            self._active = self._select_backend()
        result = self._active.embed(texts)
        # 如果当前后端失败，尝试切换
        if not result and self._active is not self._api:
            self._active = self._api
            result = self._active.embed(texts)
        return result

    @property
    def name(self) -> str:
        if self._active:
            return self._active.name
        return "hybrid(not_initialized)"


# 模块级单例
_embedding_instance: HybridEmbedding | None = None


def get_embedding() -> HybridEmbedding:
    """获取全局 embedding 客户端（懒加载单例）。"""
    global _embedding_instance
    if _embedding_instance is None:
        _embedding_instance = HybridEmbedding()
    return _embedding_instance
