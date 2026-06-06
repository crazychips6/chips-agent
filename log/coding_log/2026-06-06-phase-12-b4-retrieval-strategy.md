## 重要：Phase 12 B4 — 可切换的 RetrievalStrategy

新增 `memory/retrieval.py`，定义检索策略协议 + 实现方案 A，预留方案 B 扩展点。

### RetrievalStrategy Protocol

```python
class RetrievalStrategy(Protocol):
    def search(query, top_k=5) -> list[dict]: ...
    def add_text(text, metadata) -> int: ...
    def remove(id): ...
    def count() -> int: ...
```

### MemoryStore 接入

`prefetch()` 优先级链：
```
1. retrieval_strategy.search(query)   ← 策略优先（如 FTS5WeightedRetrieval）
2. embedding_service + vector_store   ← 次优（纯 dense）
3. 文件快照全量返回                  ← 兜底
```

`_auto_index()` 同步策略：
```
1. retrieval_strategy.add_text()   ← 策略优先
2. embedding_service + vector_store ← 降级
```

### 方案 A：FTS5WeightedRetrieval

全本地、零外部调用。三个信号源融合排序：

```
score = (norm_bm25 + weight_bonus) * temporal_decay

norm_bm25     = 1/(1+e^(bm25/5))       — FTS5 BM25 归一化
weight_bonus  = log(1+weight)*factor   — 重要性权重 0~1
temporal_decay = 0.5^(age_days/half_life) — 半衰期衰减（默认 30 天）
```

BM25 来自 FTS5 全文索引，创建时自动同步（AFTER INSERT / UPDATE / DELETE 触发器）。

默认参数：
- `weight_factor=0.5` — 权重的影响力
- `decay_half_life=30.0` — 30 天半衰期（30 天前的记忆分数减半）

### 方案 B：HybridRetrieval（骨架）

预留扩展点，待 Plan B 实现：
- BM25(Tavily) — web/知识检索
- embedding — 语义检索
- 知识图谱 — 实体关系
- RRF + reranker — 融合 + 精排

### 测试

`test/test_retrieval.py` — 17 条，覆盖：
- Score 函数（BM25 归一化、权重加成、时间衰减、三因子组合）
- Protocol 一致性
- CRUD（add/count/remove/metadata）
- 搜索（基本、空查询、top_k限制、权重排序）
- 时间衰减（新旧排序、半衰期参数效果）
- 持久化（关闭重开数据保留）
