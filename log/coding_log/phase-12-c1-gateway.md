## 重要：C1 — Model Gateway 抽象层

- 新增 `gateway/` 目录，定义 `ModelGateway` ABC（`chat()` + `chat_stream()` + on_chunk 回调），`ChatResult` dataclass 统一 LLM 返回结构
- 新增 `gateway/providers/openai.py` — `OpenAIProvider`，将 loop.py 中 `_call_llm`/`_call_llm_streaming`/`_call_with_retry` 三条内部方法迁移至此，作为独立的 provider，保留指数退避 + jitter + 流式累加 + usage 统计
- 新增 `gateway/rate_limit.py` — `TokenBucket` 令牌桶，支持 acquire/wait 两种模式

### 关键设计

`ChatResult` 的 `tool_calls` 采用 OpenAI function-calling dict 格式，替代之前的 `SimpleNamespace` 方式：

```python
@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict] | None  # [{"id", "type", "function": {"name", "arguments"}}]
    reasoning_content: str | None
    usage: dict | None  # {"prompt_tokens", "completion_tokens"}
    model: str
    latency_ms: int
```

```python
class ModelGateway(ABC):
    def chat(self, messages, model="", **kwargs) -> ChatResult: ...
    def chat_stream(self, messages, model="", *, on_chunk=None, **kwargs) -> ChatResult: ...
```

### loop.py 解耦

- 移除 `import openai` / `from openai import OpenAI` / `from agent.retry import jittered_backoff`
- `AIAgent.__init__` 接受可选 `gateway: ModelGateway | None`，未传入时回退到 `OpenAIProvider(api_key, base_url)`
- `run_conversation` 中 `msg = self._call_llm_streaming(kwargs)` 变为 `result = self.gateway.chat_stream(messages=..., on_chunk=...)`
- 流式输出不再由 provider 内 `print()` 控制，改为 `on_chunk` 回调，loop.py 负责 UI 输出

## 普通：测试迁移

- 移除 `test/test_loop.py` 中的 `TestCallWithRetry`（4 个）和 `TestStreaming`（2 个）—— 这些方法已移到 provider
- 新增 `test/gateway/test_openai_provider.py`，覆盖 OpenAIProvider.chat / chat_stream / 重试逻辑 / TokenBucket
- `mock_openai` fixture 全部替换为 `mock_gateway`（MagicMock 模拟 ModelGateway 接口）

## 细微

- `_build_assistant_msg` 增加 dict/MagicMock 双兼容（向后兼容 MagicMock 测试，向前兼容 ChatResult）
- `tools` 参数不再混入 kwargs dict，作为具名参数传给 gateway
