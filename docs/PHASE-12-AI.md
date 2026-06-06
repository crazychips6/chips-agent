# chips Phase 12 — AI 能力补齐

## 背景

Phase 10 完成后 chips 已具备"日常可用"的基础（REPL、Session、Config、记忆、Web、安全审批）。
但与 Hermes 相比，在 **AI 核心能力** 上仍有明显短板：

1. **多模态** — 模型支持图像但 chips 发不了
2. **向量记忆** — 全量塞 prompt 臃肿，无法语义检索
3. **Gateway/多模型** — 单一 base_url，无 fallback、无统计
4. **插件系统** — 加工具必须改源码
5. **上下文管理** — 粗暴裁剪，无语义判断
6. **可观测性** — 无 token/费用/耗时追踪

Phase 12 的目标是**补齐这 6 项，缩小与 Hermes 在 AI 能力上的差距**。

---

## A — 多模态支持

最高优先级的用户可见改进。让 chips 能发送图片给支持 vision 的模型。

### A1 — Content Block 抽象

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/message.py` **(新)** | 消息结构散落在 `loop.py` 里 | `ContentBlock` 联合类型：`TextBlock(content: str)` / `ImageBlock(url: str, detail: str)` |
| `agent/message.py` | `messages` 是 `list[dict]` 裸字典 | 提供 `to_openai_dicts()` 序列化，兼容 OpenAI SDK 格式 |

### A2 — 多模态消息管道

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/loop.py:_build_messages` | 只拼 text content | 检测 `Message.content` 是否为 `list[ContentBlock]`，`image_url` 正确序列化 |
| `agent/loop.py` | 无 vision 能力检测 | `model_capabilities` 配置/探测，非 vision 模型自动拒绝 image block |

### A3 — 工具支持

| 工具 | 现状 | 目标 |
|------|------|------|
| `tool/builtins/screenshot.py` **(新)** | 无 | `screenshot` 工具：截屏保存 → 返回 image_path |
| 与 REPL 集成 | 无法传递图像 | 用户拖入/粘贴图像 → `image_url` content block |

### A4 — 存储与回放

| 模块 | 现状 | 目标 |
|------|------|------|
| `session/db.py` | content 是 TEXT，存不了多模态 | 支持 `content` 为 JSON（`[{"type":"text"...}, {"type":"image_url"...}]`） |
| `agent/cli.py` `--resume` | 恢复时只能回放文本 | 回放多模态消息时略过 image block 或显示 `[图片]` 占位 |

**参考**: Hermes `hermes_core/message/` (ContentBlock 类型体系)

---

## B — 向量记忆 (语义检索)

将记忆从"全量塞 prompt"升级为"语义检索注入"。

### B1 — Embedding 服务抽象

| 模块 | 现状 | 目标 |
|------|------|------|
| `memory/embedding.py` **(新)** | 无 | Embedding Protocol：`embed(texts: list[str]) → list[list[float]]` |
| `memory/embedding.py` | 无 | 内置实现：OpenAI Embedding API / 本地 lightweight 模型 |
| `config/store.py` | 只有 model/base_url | 新增 `embedding_provider` / `embedding_model` 配置项 |

### B2 — 向量存储

| 模块 | 现状 | 目标 |
|------|------|------|
| `memory/vector.py` **(新)** | 无 | 基于 sqlite-vec 的向量存储，支持余弦相似度检索 |
| `memory/vector.py` | 无 | `add(namespace, text, embedding)` / `search(namespace, query_embedding, top_k)` |
| `memory/vector.py` | 无 | 自动维护索引（增删改） |

### B3 — 记忆管线

| 模块 | 现状 | 目标 |
|------|------|------|
| `memory/store.py` 或新模块 | `save_context()` 只做快照写 | 每轮对话后自动：提取关键信息 → embedding → 存入 vector store |
| `memory/store.py` | `for_system_prompt()` 返回全部记忆 | 改为：embed query → 检索 top-5 → 注入 system prompt |
| `memory/store.py` | 单文件快照 | 三级记忆：working（当前会话）+ episodic（历史摘要）+ semantic（向量检索） |

**参考**: Hermes `agent/memory_manager.py` 记忆层级 + embedding 管线

---

## C — Gateway / 多模型管理

从"单点调用"升级为"多模型网关"。

### C1 — Model Gateway 抽象

| 模块 | 现状 | 目标 |
|------|------|------|
| `gateway/` 目录 **(新)** | 只有 `agent/loop.py` 直接调用 OpenAI SDK | ModelGateway Protocol：`chat(messages, model, **kwargs) → ChatResult` |
| `gateway/provider.py` **(新)** | 无 | provider 实现：OpenAI / Anthropic / DeepSeek / Ollama |
| `gateway/rate_limit.py` **(新)** | 无 | 令牌桶 + 队列，超限请求排队不抛异常 |

### C2 — Fallback 与容错

| 模块 | 现状 | 目标 |
|------|------|------|
| `gateway/fallback.py` **(新)** | 无 | 模型 A 超时/限流 → 自动切模型 B（可配置 fallback chain） |
| `gateway/fallback.py` | 无 | 健康检测：定期 ping 模型，摘除不健康的 |

### C3 — 用量与统计

| 模块 | 现状 | 目标 |
|------|------|------|
| `gateway/stats.py` **(新)** | 无 | 每次调用记录：model、tokens、latency、cost、timestamp |
| `gateway/stats.py` | 无 | 聚合统计：今日 token 数、预估费用、各模型延迟对比 |

### C4 — 配置与 CLI

| 模块 | 现状 | 目标 |
|------|------|------|
| `config/store.py` | `model` + `base_url` 两个键 | 支持多模型配置：`models.primary`, `models.fallback`, `models.embedding` |
| `config/cli.py` | `get/set/list` | `chips model list` / `chips model switch <name>` |
| `agent/cli.py` | `--model` 参数覆盖 | `--model` 支持别名（如 `--model fast` 指向配置中的快速模型） |

**参考**: Hermes `hermes_core/gateway/` 全目录 — gateway 是 Hermes 最核心的抽象层

---

## D — 插件系统

从"改源码加工具"升级为"按目录放置即可发现"。

### D1 — 插件协议

| 模块 | 现状 | 目标 |
|------|------|------|
| `plugins/` 目录 **(新)** | 无 | 定义 Plugin Protocol：ToolPlugin 和 HookPlugin 两类接口 |
| `plugins/manager.py` **(新)** | 无 | 扫描 `~/.chips/plugins/` + `./plugins/` + pip-installed 插件 |

插件接口：

```python
class ToolPlugin(Protocol):
    """提供工具的插件"""
    name: str
    description: str
    def tool_definitions(self) -> list[dict]: ...
    async def execute(self, tool_name: str, args: dict) -> str: ...

class HookPlugin(Protocol):
    """挂钩到 agent 生命周期的插件"""
    def on_register(self, registry): ...
    def on_tool_call_pre(self, tool_name: str, args: dict) -> dict | None: ...
    def on_tool_call_post(self, tool_name: str, result: str) -> str | None: ...
    def on_response(self, response: str) -> str | None: ...
```

### D2 — 挂钩点

| 挂钩 | 触发时机 | 用途 |
|------|----------|------|
| `on_tool_call_pre` | 工具调用前 | 参数校验、注入、改写 |
| `on_tool_call_post` | 工具调用后 | 结果审计、记录、转换 |
| `on_approval_request` | 审批弹窗前 | 自动审批规则 |
| `on_session_end` | 对话结束 | 后处理、通知 |

### D3 — 插件管理 CLI

| 命令 | 说明 |
|------|------|
| `chips plugin list` | 列出已安装插件 |
| `chips plugin install <path-or-package>` | 安装插件（复制到 plugins 目录或 pip install） |
| `chips plugin remove <name>` | 卸载插件 |
| `chips plugin info <name>` | 查看插件详情 |

### D4 — 迁移现有工具

| 工具 | 当前 | 迁移后 |
|------|------|--------|
| `file_*` | `tool/builtins/file.py: register()` | 注册为内置 ToolPlugin |
| `web_*` | `tool/builtins/web.py: register()` | 注册为内置 ToolPlugin |
| `terminal` | `tool/builtins/terminal.py` | 注册为内置 ToolPlugin |
| `memory` | `tool/builtins/memory.py` | 注册为内置 ToolPlugin |

**参考**: Hermes `hermes_core/plugins/` 全目录 + `hermes_cli/plugins_cmd.py`

---

## E — 上下文管理 (语义级修剪)

从"FIFO + 截断"升级为"内容价值驱动的裁剪"。

### E1 — 轨迹压缩器

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/loop.py:_maybe_trim_context` | FIFO 删最早，可能砍 tool_call chain | 保护首尾 + 压缩中间 tool 结果为摘要，不丢 assistant/tool 配对 |
| `agent/trajectory_compressor.py` **(新)** | 无 | 配置 token 预算 → 超出时用 LLM 对历史分段摘要 |

### E2 — 语义裁剪

| 功能 | 说明 |
|------|------|
| 轮次价值评估 | 用 LLM 判断每轮对话的价值（high/med/low） |
| 低价值轮次丢弃 | token 超预算时优先丢弃 low 价值轮次 |
| 高价值轮次摘要 | 对 high 价值但太长的轮次做摘要保留 |

### E3 — 预算管理

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/token_budget.py` **(新)** | 无 | 可配置的 token 预算：`max_context_tokens`（默认 32K） |
| `agent/token_budget.py` | 无 | 不同模型不同预算（gpt-4 128K vs deepseek 64K） |

### E4 — 集成路径

```
_build_messages()
  → token_budget.check(total_tokens)
  → if 超出:
      trajectory_compressor.compress(history, budget)
       → 轮次价值评估
       → 低价值丢弃
       → 高价值摘要
  → return compressed messages
```

**参考**: Hermes `hermes_core/agent/trajectory_compressor.py` + token_budget 机制

---

## F — 可观测性 (Token/费用/耗时追踪)

| 模块 | 现状 | 目标 |
|------|------|------|
| `agent/loop.py` | 无统计 | 每次 LLM 调用记录：token 数（prompt + completion）、耗时、模型、费用估算 |
| 新模块或 `session/` | 审计日志有操作记录无费用 | 每次调用写入 `usage_log` 表：`(timestamp, model, prompt_tokens, completion_tokens, latency_ms, cost_estimate, session_id)` |
| `session/db.py` | `sessions` + `messages` | 新增 `usage_log` 表 |
| `session/cli.py` | `list/show/search/delete` | `chips session stats <id>` — 显示会话 token 消耗、费用、平均延迟 |
| `agent/cli.py` | 无统计输出 | 退出时打印本次会话统计摘要 |

### F1 — Token 计数器

| 模块 | 说明 |
|------|------|
| `agent/token_counter.py` **(新)** | 准确计数（用 tiktoken / tokenizers 库），非估算 |

### F2 — Usage Log

```sql
CREATE TABLE IF NOT EXISTS usage_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    model        TEXT NOT NULL,
    prompt_tokens   INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    latency_ms   INTEGER NOT NULL,
    cost_estimate REAL,
    created_at   REAL NOT NULL
);
```

### F3 — 模型费用配置

```yaml
# ~/.chips/config.yaml
models:
  pricing:
    deepseek-chat:
      input: 0.00014   # $ per 1K prompt tokens
      output: 0.00028  # $ per 1K completion tokens
```

## 实施顺序

```
Phase 12 依赖关系：

A (多模态) ─── 独立，可先做
                 │
B (向量记忆) ─── 独立，可先做
                 │
C (Gateway) ──── 独立，但 F(可观测性) 依赖 C3 stats
                 │
D (插件系统) ─── 独立，但内置工具迁移放到最后
                 │
E (上下文管理) ─ 独立，可先做
                 │
F (可观测性) ──── 依赖 C3（用量统计），但 F1+F2 可独立先做
```

**建议实施顺序**：

1. **D1+D2**（插件协议 + 管理器）— 基础设施，之后新增工具都可以走插件
2. **A1+A2**（多模态）— 用户最可见的改进
3. **B1+B2**（向量记忆）— 最大质量改进
4. **C1+C2**（Gateway fallback）— 可靠性改进
5. **E1+E2**（上下文管理）— 长会话保障
6. **F1+F2**（可观测性）— 数据到位
7. **A3+A4**（多模态工具 + 存储）
8. **B3**（记忆管线集成）
9. **C3+C4**（用量统计 + CLI）
10. **D3+D4**（插件 CLI + 工具迁移）

## 交付标准

与 Phase 10 一致：**每个子阶段有对应测试，全量测试通过，新增功能可手动验证**。
