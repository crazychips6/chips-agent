# Phase 5 — System Prompt 完整组装

## 重要

- 重写 `agent/prompt.py`：7 层 PromptBuilder 结构
  - 层 (1) 核心身份 — 你是谁的基本身份 + 行为准则
  - 层 (2) 当前日期 — `datetime.date.today()` 注入，LLM 知晓当前时间
  - 层 (3) 用户偏好 — 来自 memory USER.md（可选层，有内容才显示）
  - 层 (4) 记忆快照 — 来自 memory MEMORY.md（可选层）
  - 层 (5) 项目上下文 — CLAUDE.md / .claude/CLAUDE.md / CONTEXT.md 文件内容（可选层）
  - 层 (6) 工具规则 — registry 中已注册工具的说明列表（可选层）
  - 层 (7) 调用约定 — 回复规范
- `search_context_files()`：从 CWD 向上搜索至 git 根目录，收集 CLAUDE.md / .claude/CLAUDE.md / CONTEXT.md
- `detect_injection()`：8 组正则检测中英文 prompt injection，命中则跳过该文件并输出警告
- `_assemble_and_truncate()`：超出 `max_prompt_chars`（默认 6000）时保头保尾截中间
- `verbose` 模式：`--verbose` 参数触发 `_dump_layers()`，输出各层字符数和行数到 stderr，仅在首次构建时输出

## 普通

- 更新 `agent/loop.py`：AIAgent 接受 `verbose` 参数 → 传给 PromptBuilder；`run_conversation` 从 MemoryStore 获取原始数据后调用 7 层 builder
- 更新 `agent/cli.py`：新增 `--verbose` 参数；启动时调用 `search_context_files()` 搜索上下文文件并注入 agent；启动面板显示上下文文件数
- 更新 `memory/store.py`：新增 `get_all()` 返回原始快照 dict，供 PromptBuilder 按 category 分 layer 注入
- 创建 `test/test_prompt.py`：25 个测试，覆盖注入检测 8 种模式、上下文文件搜索 6 种场景、Builder 13 种场景（各层/条件隐藏/截断/verbose）

## 细微

- `search_context_files()` 以 git 根目录为上界，避免搜到 `~/.claude/` 下的个人配置
- Injection 模式包括中英文：忽略指令、角色扮演、忘记设定、系统覆盖、角色重定义
- 截断策略：始终保前 2 层 + 后 2 层，中间层截断并添加 `...(中间内容已截断)...` 标记
- 上下文文件跳过空文件和注入命中文件，不中断启动流程
