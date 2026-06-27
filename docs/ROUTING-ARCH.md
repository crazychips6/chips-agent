# chips 路由架构

> 状态: 已实现 | 版本: v1 | 更新: 2026-06-22

## 架构全景

```
用户输入
  │
  ├─ ① 入口层 — 单 Agent 优先
  │   "Start with one agent whenever you can." — OpenAI
  │   所有流量默认由主 Agent 处理
  │
  ├─ ② RuleEngine（规则引擎）— ~0ms
  │   ├─ 提取低代价事实（字符串匹配/正则）
  │   ├─ 按 priority 降序遍历规则
  │   ├─ 命中 → 返回 RouteDecision
  │   └─ 未命中 → 进下一层
  │
  ├─ ③ LLM Router — 1 次 LLM 调用，~500ms
  │   ├─ structured output 分析任务
  │   ├─ 输出 action / target_agent / plan
  │   └─ 失败 → 安全降级 direct
  │
  └─ ④ Fallback — direct
      走主 Agent ReAct 循环
```

## 三层递进

| 层级 | 延迟 | 调用频次 | 覆盖场景 | 失败行为 |
|------|------|---------|---------|---------|
| RuleEngine | ~0ms | 每轮 | 明确意图的 60% 场景 | direct |
| LLM Router | ~500ms | 规则引擎未命中时 | 模糊/复杂/跨领域的 30% 场景 | direct |
| Fallback | 0 | 前两层都未命中 | 默认的 10% 场景 | direct |

## 路由模式

| 模式 | 状态 | 说明 |
|------|------|------|
| **direct** | ✅ | 主 Agent 直接处理 |
| **delegate** | ✅ | 委派给子 Agent |
| **block** | ✅ | 拦截（安全规则） |
| **llm_router** | ✅ | LLM 分析决策 |
| **orchestrate** | ✅ | 编排执行（多 Agent 协作） |
| **handoff** | ⏳ | 控制权转移（已定义，未实现） |

## 核心组件

### rules/ 目录

| 文件 | 职责 |
|------|------|
| `models.py` | Rule / RouteDecision / Condition 数据模型 |
| `facts.py` | FactRegistry + 内建 Fact Extractor（10 个） |
| `engine.py` | RuleEngine（三层匹配 + 统计） |
| `loader.py` | YAML 加载 + 同名覆盖 + 合并策略 |
| `llm_router.py` | LLMRouter（结构化输出分析） |
| `defaults.yaml` | 7 条默认规则 |

### 规则即数据

规则写在 YAML 中，引擎是通用解释器：

```
~/.chips/routing.yaml   ← 用户自定义（同名覆盖默认）
rules/defaults.yaml      ← 内置默认规则（低优先级）
```

合并策略：同名规则用户覆盖默认，不同名合并。按 priority 降序排列。

### 优先级设计

优先级数字本身无含义，只用来比大小。段位设计：

| 段位 | 优先级范围 | 用途 |
|------|-----------|------|
| 安全 | 5000-1000 | 拦截/block |
| 路由 | 200-100 | delegate 到 Specialist Agent |
| 效率 | 50-10 | 短消息/简单问题直接处理 |
| 兜底 | 20-1 | LLM Router 分析 |

段位间留空隙，方便用户插入新规则而不影响现有规则。

### Fact 系统

FactRegistry + FactExtractor 注册表模式：

```python
@FactRegistry.register("intent_keyword", cost="low", ...)
def _extract_intent_keyword(text: str) -> list[str]:
    ...
```

代价分层：

| 代价 | 调用策略 | 延迟 |
|------|---------|------|
| low | 始终提取 | ~0ms |
| medium | 仅在 low 匹配失败后 | ~0ms |
| high | 暂未使用 | ~500ms |

## 集成点

- `agent/loop.py` — `run_conversation()` 入口处规划阶段
- `agent/cli.py` — `--auto-plan` 标志 + `chips router` 子命令
- `_wire_auto_plan()` 函数注入 RuleEngine + LLMRouter

## CLI 子命令

| 命令 | 用途 |
|------|------|
| `chips router list` | 列出所有规则 |
| `chips router test` | 运行规则测试用例 |
| `chips router summary` | 显示引擎统计 |
| `chips router init` | 生成 ~/.chips/routing.yaml |
| `chips router reload` | 重新加载规则 |

## 安全降级链

任何层级失败都不会影响用户体验：

```
RuleEngine 异常 → direct（走 ReAct）
LLM Router 解析失败 → direct（走 ReAct）
Gateway 不可用 → direct（走 ReAct）
子 Agent 创建失败 → direct（走 ReAct）
```

## 未来规划

### P0 — 补齐缺口

| 方向 | 说明 |
|------|------|
| Handoff 模式 | `_execute_auto_plan()` 的 handoff 动作目前回退 ReAct，需要真正实现控制权转移 |
| 路由可观测性 | 规则命中/未命中/LLM Router 调用 → Prometheus 指标 + `chips router summary` 持久化到 SQLite |

### P1 — 效果优化

| 方向 | 说明 |
|------|------|
| 上下文感知路由 | LLM Router 现在只看当前消息。结合最近 3 轮对话摘要，对"基于刚才讨论的内容做xxx"这类任务判断更准 |
| 路由反馈闭环 | 用户可以对路由决策给反馈（"不对，不应该走 researcher"），反馈自动沉淀为规则 |

### P2 — 工程完善

| 方向 | 说明 |
|------|------|
| 规则热加载 | watchdog 监听 routing.yaml 变更，自动重载，无需重启 |
| 规则测试基础设施 | 默认规则写测试用例 + CI 集成 |
| 性能缓存 | LRU 缓存 Fact 提取结果，同一轮次避免重复计算 |
