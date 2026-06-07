# 装饰器模式 — 在抽象之上无侵入加功能

## 场景

已有 `ModelGateway` 抽象层 + `OpenAIProvider` 实现。现在要加用量统计，但不改 AIAgent 和 OpenAIProvider 的代码。

## 做法

装饰器实现同样的接口，内部持有真实 provider：

```
之前: agent.gateway = OpenAIProvider(...)
之后: agent.gateway = UsageRecorder(OpenAIProvider(...))
```

AIAgent 调 `gateway.chat()` → 实际调的是 `UsageRecorder.chat()` → 记录用量 → 透传给 `OpenAIProvider.chat()`。

## 关键

- **上下游都不知情** — AIAgent 不知道有记录器，OpenAIProvider 也不知道自己被包了一层
- **零改动** — 加功能不需要改已有类的代码，只改组装线（`cli.py`）
- **可叠加** — 加限流、缓存、重试统计都可以用同样的方式叠：

```python
gateway = RateLimiter(UsageRecorder(OpenAIProvider(...)))
```

## 和抽象的区别

| 抽象 | 装饰器 |
|------|--------|
| 解决"多个实现"的问题 | 解决"给单个实现加横切关注点"的问题 |
| 实现互换 | 实现不变，加外层逻辑 |
| 调用方知道接口 | 调用方和实现方都不知道中间层 |
