## 重要：Phase 12 B2 — 向量存储

### 新增模块
- `memory/vector.py` — 基于 SQLite + JSON + 纯 Python 余弦相似度的向量存储

### 设计

`VectorStore` 类：
- 多 namespace 隔离（memory / episodic / user 各自独立索引）
- 使用 SQLite WAL 模式，写操作串行化（与 SessionDB 一致的模式）
- Embedding 向量存为 JSON array 文本列
- 检索时全量扫描 + 余弦相似度排序（纯 Python，无外部依赖）

```python
vs = VectorStore("path/to/vectors.db")
vs.add("memory", "文本", [0.1, 0.2, ...], metadata={"source": "user"})
results = vs.search("memory", query_embedding, top_k=5)
# 返回: [{id, namespace, text, score, metadata, created_at}, ...]
```

### 性能参考（纯 Python 线性扫描）
- 1K 条 / 1536 维: ~5ms
- 10K 条 / 1536 维: ~50ms

对个人 agent 的记忆规模完全足够，不需要引入 numpy/faiss。

### 测试
`test/test_vector.py` — 15 条，覆盖：
- 余弦相似度（相同、相反、正交、零向量）
- 增删改查（add / delete / count / list_namespaces / delete_namespace）
- 搜索排序（最相关排最前、top_k 限制、namespace 隔离、score 范围）
- 批量添加
- 持久化（关闭后重新打开数据仍在、空路径初始化不崩溃）
