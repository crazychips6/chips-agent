## 重要：Phase 12 B1 — Embedding 服务抽象

### 新增模块
- `memory/embedding.py` — Embedding 服务抽象层

### 设计

`EmbeddingProtocol`（运行时协议检查）：
```python
class EmbeddingProtocol(Protocol):
    def embed(texts: list[str]) -> list[list[float]]: ...
    dimensions -> int
    model_name -> str
```

`OpenAIEmbedding` — OpenAI Embedding API 的实现：
- 默认模型 `text-embedding-3-small`（1536 维）
- 支持批量请求、自动重试（httpx，利用 openai 的传递依赖）
- 兼容 OpenAI API 格式，可对接任何兼容的 embedding 服务

### 设计决策
- 使用 `httpx.Client` 直连 API（而非 openai SDK），避免多余的依赖层
- Runtime 协议检查通过 `@runtime_checkable` 实现，方便测试 mock
- 失败静默降级：embedding 不可用不影响主流程

### 配置
`config/store.py` 新增三个键值对：
- `embedding_provider` / `CHIPS_EMBEDDING_PROVIDER`
- `embedding_model` / `CHIPS_EMBEDDING_MODEL`
- `embedding_base_url` / `CHIPS_EMBEDDING_BASE_URL`

### 测试
`test/test_embedding.py` — 12 条，覆盖：
- 初始化（缺 key、自定义参数）
- 单条/批量/空列表请求
- 重试成功 + 重试耗尽
- Protocol 一致性检查
