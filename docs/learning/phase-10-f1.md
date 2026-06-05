# Phase 10 F 学习笔记

## 三级记忆模型 (working / episodic / semantic)

| 层级 | 存储 | 生命周期 | 用途 |
|------|------|---------|------|
| Working | 内存 dict | 当前会话 | 结构化笔记，`key: value` |
| Episodic | EPISODIC.md | 跨会话 | 历史摘要，自动带时间戳 |
| Semantic | MEMORY.md + USER.md | 永久 | 持久知识 |

## Snapshot 模式的局限

当前实现是 **启动时全量读取 → 全部塞进 system prompt**。缺点：

1. 记忆越多，prompt 窗口越浪费（不相关的也在占空间）
2. 有硬上限（prompt 长度限制）
3. 无法根据当前查询只取相关记忆

## 主流方案对比

### 向量记忆（更主流）
- 所有记忆 embedding 后存入向量数据库
- 不需要预加载：收到用户问题后，embedding → 搜 top-k → 只注入相关的 3-5 条
- 代表：Mem0、LangChain Memory、AutoGPT (Pinecone)
- 好处：记忆量理论上无限，不浪费 prompt 窗口
- 代价：需要 embedding 模型 + 向量数据库

### KV 存储
- 简单的键值对，Agent 自己决定存什么查什么
- 相当于当前 working memory 在做的事

### 混合方案
- 向量做长期检索 + 滑动窗口做短期上下文
- 很多生产项目的实际选择

## 一句话总结

当前三层模型**结构清晰、实现简单**，但不向量化就无法按语义检索，记忆越多越臃肿。向量记忆是更主流的长期记忆方案。
