# 解耦的一般步骤

> 总结自 C1 Gateway 抽象的经验

## 什么时候该解耦

```
收益 = 变动频率差 × 调用方数量 × 测试收益
成本 = 间接层数 × 代码分散度
要不要做 = 收益 > 成本
```

主要考量：

1. **变动频率和方向** — A 和 B 以不同节奏变化时值得解耦。loop.py 几乎不变，LLM provider 可能会频繁加
2. **测试难度** — 如果依赖具体实现导致调用方难测，就该解耦
3. **调用方数量** — 被调用的地方越多越值得抽
4. **模块边界规则** — 防止循环依赖，硬性约束
5. **业务规模** — Rule of Three：等到确实需要第二个实现时再抽接口

## 解耦的 7 步

### 1. 识别边界

找出调用方需要什么、提供方给什么。边界画错了后面全歪。

> 之前：loop.py 直接调 OpenAI SDK，耦合的是"发消息给 OpenAI 拿回复"
> 边界应该是："发消息拿回复"，不关心是不是 OpenAI

### 2. 定义契约

抽象类和结果格式。只描述"什么"，不描述"怎么"。

```python
class ModelGateway(ABC):
    def chat(self, messages, model="", **kwargs) -> ChatResult: ...

@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict] | None
    ...
```

坏的契约会把 provider 特有的参数带到接口里（比如 `openai_api_key`）。

### 3. 拆现有实现

把交织在调用方里的提供方逻辑拆到具体类中。先搬再改，不要顺手改行为。

> 这次 C1：`_call_with_retry` / `_call_llm` / `_call_llm_streaming` 三条方法从 loop.py 搬到 `OpenAIProvider`

### 4. 修改调用方

调用方通过契约调用，不再 import 任何提供方实现。

> 之前：`msg = self._call_llm_streaming(kwargs)`
> 之后：`result = self.gateway.chat_stream(messages=..., on_chunk=...)`

### 5. 验证

测试通过，调用方的测试只 mock 契约，不 mock 具体实现。

> mock_openai → mock_gateway，mock 链从 4 层（`client.chat.completions.create.return_value.choices[0].message`）降为 1 层（`ChatResult`）

### 6. 替调用方擦屁股

调用方原来传的 provider 特有参数要处理：给默认值，或通过 `**kwargs` 透传（不完全解耦但实用）。

### 7. 检查旧入口

解耦后的旧构造方式是否要保留？理想情况下最终应去掉，强制调用方显式传入依赖。
