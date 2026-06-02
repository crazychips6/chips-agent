## 重要：file_read / file_write 内置工具

- `tool/builtins/file.py` — 文件读写工具，路径安全校验内联实现（不违反 tool/ 零依赖约束）
- `file_read`：敏感路径匹配（`.env`、`*.pem`、`*id_rsa*`、`.chips/` 等），不可读
- `file_write`：写入路径保护（`/etc/`、`/usr/`、`.git/`、`.chips/` 等），不可写；支持 write/append 两种模式
- 注册在 core 工具集，LLM 可直接使用

## 普通：Context 压缩

- `agent/loop.py` — `_maybe_trim_context()` 在每次 LLM 调用前执行
- 阈值 `max_context_chars = 100_000`（约 30k tokens）
- 超限后从最旧消息开始逐条删除，保留最近 2 条

## 普通：测试覆盖补全

- `test/test_builtins.py` — 新增 TestFileTool（9 条覆盖：读写往返、追加、敏感路径拒绝、注册校验）
- `test/test_environment.py` — 新增 TestLocalEnvironment（11 条覆盖：安全/危险命令、超时、stderr、凭证剥离、close）
- 全量 185 条全部通过

## 细微：标记阶段 8 完成
- 沙盒环境（Environment Protocol + LocalEnvironment + terminal 工具）实际已在阶段 6 完成
