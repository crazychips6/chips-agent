# Phase 1 — 最小 ReAct 循环

## 重要

- 创建 `agent/loop.py`：`AIAgent` 类，基于 `openai` SDK 调用 LLM，支持 `base_url` 和 `model` 配置
- 创建 `agent/prompt.py`：`PromptBuilder` 返回默认系统身份 prompt
- 重写 `agent/cli.py`：交互式 input 循环 + `.env` 配置加载 + `--message` 单次模式
- 切换模型 SDK：`anthropic` → `openai`（兼容 DeepSeek API）
- 创建 `.env`：集中管理 `DEEPSEEK_API_KEY`、`CHIPS_BASE_URL`、`CHIPS_MODEL`
- 创建 `.gitignore`

## 普通

- `pyproject.toml`：依赖改为 `openai` + `python-dotenv`
- `agent/__init__.py`：更新注释

## 细微

- 清理 `cli.py` 中不再使用的 `pathlib` 导入
