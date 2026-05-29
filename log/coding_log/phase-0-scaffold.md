# Phase 0 — 项目脚手架

## 重要

- 创建 `pyproject.toml`，配置 uv flat-layout 项目，依赖 `anthropic`
- 创建全部模块顶层目录：`agent/`, `tool/`, `safety/`, `environment/`, `session/`, `memory/`, `test/`
- 编写最小 CLI 入口 `agent/cli.py`（argparse, --model, --version, --help）
- 创建 `docs/PLAN.md` 完整分阶段实施计划（10个阶段，强解耦架构）
- 创建 `log/coding_log/` 目录
- 更新 `CLAUDE.md` 反映完整项目结构

## 普通

- 为每个模块创建 `__init__.py`
- 为 `tool/builtins/` 创建 `__init__.py`

## 细微

- 目录结构调整：从 `src/chips/` 嵌套改为顶层包（对齐 CLAUDE.md 规范）
- `pyproject.toml` 增加 `tool.uv.package` 和 `build-system` 配置
