"""Embedding 服务抽象

B1: Embedding Protocol + OpenAI Embedding 实现。
零项目内部依赖（除 config 可选读取 base_url 外）。"""

from __future__ import annotations

import json
import os
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProtocol(Protocol):
    """Embedding 服务协议。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """将文本列表转为 embedding 向量列表。"""

    @property
    def dimensions(self) -> int:
        """返回 embedding 向量的维度。"""

    @property
    def model_name(self) -> str:
        """返回使用的模型名称。"""


class OpenAIEmbedding:
    """OpenAI Embedding API 实现。

    默认模型 text-embedding-3-small（1536 维），成本 ~$0.02/1M tokens。
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        max_retries: int = 3,
    ):
        self._api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimensions = dimensions
        self._max_retries = max_retries

        if not self._api_key:
            raise ValueError(
                "OpenAI API key 未设置。请设置 OPENAI_API_KEY 环境变量或通过 api_key 参数传入。"
            )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_name(self) -> str:
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """调用 OpenAI Embedding API 获取向量。

        支持批量请求，自动处理空列表。带重试逻辑。
        """
        if not texts:
            return []

        import httpx

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "input": texts,
            "model": self._model,
            "dimensions": self._dimensions,
        }

        url = f"{self._base_url}/embeddings"

        last_error = None
        for attempt in range(1, self._max_retries + 1):
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                # 按输入顺序排列结果
                ordered = [None] * len(texts)
                for item in data.get("data", []):
                    ordered[item["index"]] = item["embedding"]
                return ordered
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 502, 503, 504) and attempt < self._max_retries:
                    last_error = str(e)
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Embedding API 错误: {e}")
            except (httpx.RequestError, json.JSONDecodeError) as e:
                if attempt < self._max_retries:
                    last_error = str(e)
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Embedding 请求失败: {e}")

        raise RuntimeError(f"Embedding 失败（已重试 {self._max_retries} 次）: {last_error}")
