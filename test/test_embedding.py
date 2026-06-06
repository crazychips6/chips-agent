"""memory/embedding.py 测试"""

from unittest.mock import MagicMock, patch

import pytest

from memory.embedding import OpenAIEmbedding


class TestOpenAIEmbeddingInit:
    def test_missing_api_key(self):
        with pytest.raises(ValueError, match="API key"):
            OpenAIEmbedding(api_key="")

    def test_defaults(self):
        emb = OpenAIEmbedding(api_key="sk-test")
        assert emb.dimensions == 1536
        assert emb.model_name == "text-embedding-3-small"

    def test_custom_model(self):
        emb = OpenAIEmbedding(api_key="sk-test", model="text-embedding-3-large", dimensions=3072)
        assert emb.dimensions == 3072
        assert emb.model_name == "text-embedding-3-large"

    def test_empty_texts(self):
        emb = OpenAIEmbedding(api_key="sk-test")
        assert emb.embed([]) == []


class TestOpenAIEmbeddingCall:
    def test_success(self):
        emb = OpenAIEmbedding(api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"index": 0, "embedding": [0.1, 0.2, 0.3]},
            ],
            "model": "text-embedding-3-small",
            "usage": {"total_tokens": 4},
        }

        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.post.return_value = mock_resp
            MockClient.return_value.__enter__.return_value = client

            result = emb.embed(["hello world"])

        assert len(result) == 1
        assert result[0] == [0.1, 0.2, 0.3]

    def test_batch_ordered(self):
        """批量请求按输入顺序返回。"""
        emb = OpenAIEmbedding(api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"index": 1, "embedding": [0.9, 0.9]},
                {"index": 0, "embedding": [0.1, 0.1]},
            ],
        }

        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.post.return_value = mock_resp
            MockClient.return_value.__enter__.return_value = client

            result = emb.embed(["first", "second"])

        assert result[0] == [0.1, 0.1]
        assert result[1] == [0.9, 0.9]

    def test_retry_then_success(self):
        """第一次失败，重试后成功。"""
        emb = OpenAIEmbedding(api_key="sk-test", max_retries=3)
        fail_resp = MagicMock(status_code=502, raise_for_status=MagicMock(side_effect=Exception("502")))
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"data": [{"index": 0, "embedding": [0.5]}]}

        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            from httpx import RequestError
            client.post.side_effect = [RequestError("connection error"), ok_resp]
            MockClient.return_value.__enter__.return_value = client

            result = emb.embed(["retry test"])
            assert result == [[0.5]]

    def test_retry_exhausted(self):
        """全部重试失败后抛出 RuntimeError。"""
        emb = OpenAIEmbedding(api_key="sk-test", max_retries=2)

        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            from httpx import RequestError
            client.post.side_effect = RequestError("persistent failure")
            MockClient.return_value.__enter__.return_value = client

            with pytest.raises(RuntimeError, match="Embedding 请求失败"):
                emb.embed(["fail"])


class TestProtocolConformance:
    def test_is_embedding_protocol(self):
        from memory.embedding import EmbeddingProtocol
        emb = OpenAIEmbedding(api_key="sk-test")
        assert isinstance(emb, EmbeddingProtocol)
