# Phase 0 学习笔记

## argparse

- Python **标准库**，无需安装，与 uv 无关
- `uv run chips --model xxx`：uv 只负责启动环境，参数解析仍由 argparse 完成

## 自注册理解

注册行为归属于工具自身（xxx_tool.py 调用 registry.register），而不是 agent 去管理工具。

```
工具文件 → registry.register()    ← 工具自己"报名"
agent    → registry.get_definitions() / dispatch()  ← agent 只问 registry
```

核心结论：

- 自注册解决的是**「新增工具不改 agent 代码」**
- 不解决 **schema 与 handler 的一致性验证** — 那层保障在人的审查和测试
