"""端侧模型深度集成测试"""

import json
import pytest
from unittest.mock import patch, MagicMock


class TestFastLLMToolArgs:
    """FastLLM 工具参数预生成测试。"""

    def test_generate_tool_args_not_available(self):
        """测试：Ollama 不可用时返回 None。"""
        from endpoint.fast_llm import FastLLM

        fllm = FastLLM()
        fllm._available = False

        result = fllm.generate_tool_args(
            tool_name="web",
            tool_description="搜索互联网",
            tool_schema={"query": {"type": "string"}},
            user_message="今天天气怎么样",
        )
        assert result is None

    @patch("endpoint.fast_llm.urllib.request.urlopen")
    def test_generate_tool_args_success(self, mock_urlopen):
        """测试：成功生成工具参数。"""
        from endpoint.fast_llm import FastLLM

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": '{"query": "今天天气", "language": "zh"}'}
        }).encode()
        mock_urlopen.return_value = mock_resp

        fllm = FastLLM()
        fllm._available = True

        result = fllm.generate_tool_args(
            tool_name="web",
            tool_description="搜索互联网网页",
            tool_schema={"query": {"type": "string"}, "language": {"type": "string"}},
            user_message="今天天气怎么样",
            intent="web_search",
        )
        assert result is not None
        assert result["query"] == "今天天气"

    @patch("endpoint.fast_llm.urllib.request.urlopen")
    def test_generate_tool_args_invalid_json(self, mock_urlopen):
        """测试：LLM 返回无效 JSON 时返回 None。"""
        from endpoint.fast_llm import FastLLM

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": "这不是 JSON"}
        }).encode()
        mock_urlopen.return_value = mock_resp

        fllm = FastLLM()
        fllm._available = True

        result = fllm.generate_tool_args(
            tool_name="web",
            tool_description="搜索互联网",
            tool_schema={},
            user_message="今天天气",
        )
        assert result is None


class TestFastLLMPostprocess:
    """FastLLM 工具结果后处理测试。"""

    def test_postprocess_short_result(self):
        """测试：短结果不处理。"""
        from endpoint.fast_llm import FastLLM

        fllm = FastLLM()
        fllm._available = True

        result = fllm.postprocess_tool_result(
            tool_name="web",
            tool_result="短内容",
            user_question="天气",
        )
        assert result is None

    def test_postprocess_not_available(self):
        """测试：Ollama 不可用时返回 None。"""
        from endpoint.fast_llm import FastLLM

        fllm = FastLLM()
        fllm._available = False

        result = fllm.postprocess_tool_result(
            tool_name="web",
            tool_result="x" * 1000,
            user_question="天气",
        )
        assert result is None

    @patch("endpoint.fast_llm.urllib.request.urlopen")
    def test_postprocess_success(self, mock_urlopen):
        """测试：成功后处理工具结果。"""
        from endpoint.fast_llm import FastLLM

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": json.dumps({
                "summary": "今天北京晴天，25°C",
                "key_points": ["晴天", "25度"],
                "is_relevant": True,
            })}
        }).encode()
        mock_urlopen.return_value = mock_resp

        fllm = FastLLM()
        fllm._available = True

        result = fllm.postprocess_tool_result(
            tool_name="web",
            tool_result="x" * 1000,
            user_question="今天天气怎么样",
        )
        assert result is not None
        assert result["summary"] == "今天北京晴天，25°C"
        assert result["is_relevant"] is True


class TestFastLLMQualityEval:
    """FastLLM 对话质量评估测试。"""

    def test_evaluate_short_reply(self):
        """测试：短回复不评估。"""
        from endpoint.fast_llm import FastLLM

        fllm = FastLLM()
        fllm._available = True

        result = fllm.evaluate_reply_quality(
            user_question="天气",
            ai_reply="短",
        )
        assert result is None

    def test_evaluate_not_available(self):
        """测试：Ollama 不可用时返回 None。"""
        from endpoint.fast_llm import FastLLM

        fllm = FastLLM()
        fllm._available = False

        result = fllm.evaluate_reply_quality(
            user_question="天气",
            ai_reply="x" * 100,
        )
        assert result is None

    @patch("endpoint.fast_llm.urllib.request.urlopen")
    def test_evaluate_success(self, mock_urlopen):
        """测试：成功评估回复质量。"""
        from endpoint.fast_llm import FastLLM

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": json.dumps({
                "quality_score": 0.85,
                "issues": [],
                "suggestions": ["可以更详细"],
                "has_hallucination": False,
                "is_helpful": True,
            })}
        }).encode()
        mock_urlopen.return_value = mock_resp

        fllm = FastLLM()
        fllm._available = True

        result = fllm.evaluate_reply_quality(
            user_question="今天天气怎么样",
            ai_reply="今天北京晴天，气温 25°C，适合外出活动。建议携带防晒用品，注意补水。" * 2,
        )
        assert result is not None
        assert result["quality_score"] == 0.85
        assert result["has_hallucination"] is False
        assert result["is_helpful"] is True


class TestFastLLMClassify:
    """FastLLM 分类测试（现有功能）。"""

    def test_classify_not_available(self):
        """测试：Ollama 不可用时返回 fallback。"""
        from endpoint.fast_llm import FastLLM

        fllm = FastLLM()
        fllm._available = False

        # 使用一个不会被规则匹配的消息
        result = fllm.classify("随便说点什么")
        assert result["intent"] == "other"
        assert result["source"] == "fallback"
