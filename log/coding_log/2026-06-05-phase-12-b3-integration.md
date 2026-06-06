## 重要：Phase 12 B3 — 记忆管线集成

### 改动

| 文件 | 改动 |
|------|------|
| `memory/store.py` | MemoryStore 新增 `prefetch(query, top_k)`、`_auto_embed()` |
| `agent/loop.py` | `run_conversation` 使用 `memory.prefetch(user_message)` 替代全量注入 |
| `agent/cli.py` | wiring 中创建 OpenAIEmbedding + VectorStore 并注入 MemoryStore |

### MemoryStore 新增方法

**`prefetch(query, top_k=5)`** — 向量检索主入口：
1. 查询文本 → `embedding_service.embed([query])` → 获取 query 向量
2. `vector_store.search(namespace="memory", query_embedding, top_k)` → top-k 结果
3. 无结果时降级尝试 episodic namespace
4. 完全无结果或无 embedding 服务时返回全部 memory 文本（向后兼容）
5. 格式化输出：只保留 score > 0.5 的结果（低分不注入避免干扰）

**`_auto_embed(text, category, metadata)`** — 自动 embedding：
1. 在 `add()` 之后自动触发（memory / episodic 类别）
2. working / user 类别不触发
3. embedding 失败不阻塞主流程（静默降级）

### Loop.py 集成

```python
# Before: 全量 memory 直接注入 system prompt
memory_data = self.memory.get_all()

# After: 按用户消息语义检索相关记忆
retrieved = self.memory.prefetch(user_message)
system = self.prompt_builder.build(memory=retrieved or ...)
```

### CLI Wiring

在 `agent/cli.py` 中新增：
1. 读取 `OPENAI_API_KEY`（或回退到 `DEEPSEEK_API_KEY`）作为 embedding key
2. 支持 `CHIPS_EMBEDDING_BASE_URL` / `CHIPS_EMBEDDING_MODEL` 环境变量覆盖
3. 有 API key 时创建 `OpenAIEmbedding` + `VectorStore`
4. 无可用时 MemoryStore 以降级模式运行（无向量检索）

## 普通：测试覆盖

`test/test_memory.py` 新增 2 个测试类（共 8 条）：

### TestPrefetch（4 条）
- 无 embedding 服务时降级为全量 memory
- 有 embedding 服务时返回检索结果
- 向量存储为空时回退到文件快照
- embedding API 失败时静默降级

### TestAutoEmbed（4 条）
- `add(memory)` 自动触发 embedding
- `add(episodic)` 自动触发 embedding
- `add(working)` 不触发 embedding
- embedding 失败不阻塞 add 主流程

## 细微：测试修复

- `test/test_loop.py` 中 `TestRunConversation` 的所有 Mock agent.memory 需要 mock `prefetch.return_value = ""`，否则 MagicMock 对象会作为字符串传入 PromptBuilder 导致断言失败
