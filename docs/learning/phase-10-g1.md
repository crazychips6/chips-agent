# G1 — REPL 框架

agent 问答循环本质上就是一个 REPL（Read-Eval-Print-Loop）。

之前的做法是循环代码全部写死在 `cli.py` 的 `main()` 里。现在拆成了组件：

- `ReplLoop` — 循环框架
- `InputBackend` + `OutputBackend` — 输入/输出抽象
- `CommandRegistry` — `/` 命令管理

大流程不变，但更加框架化。`prompt_toolkit` 就是替换了原来的 `input()`，提供了历史持久化、Tab 补全、多行编辑。

# G3 — 配置系统

新增 `config/store.py` 封装 `~/.chips/config.yaml` 读写。`ConfigStore` 提供 `get/set/list_all/apply_to_env`，`config/cli.py` 处理 `chips config set/get/list` 子命令。

`cli.py` 中的内联 `_load_config()` 替换为 `ConfigStore().apply_to_env()`，argparse 拆出了 `_build_parser()` 以便加子命令。
