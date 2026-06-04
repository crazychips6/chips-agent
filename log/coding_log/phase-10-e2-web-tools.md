## 重要：Phase 10 E2 — Web 工具

**新文件**: `tool/builtins/web.py`，零外部依赖（仅 stdlib）。

### web_fetch（HTTP GET）

- 自动补全 scheme（无前缀 → `https://`）
- HTML → 纯文本提取（`_HTMLTextExtractor`，跳过 script/style）
- 非 HTML 内容直接返回原始文本
- SSRF 防护：阻止 127.0.0.1/8、10.0.0.0/8、192.168.0.0/16、::1 等内网地址
- 超时 15s，响应上限 5MB，输出截断 10K 字符

### web_search（DuckDuckGo Lite）

- 免费、无需 API Key
- `_parse_ddg_results()` 解析 DDG Lite HTML 表格
- 返回标题 + URL + 摘要
- 相对 URL 自动补全，`max_results` 上限 20

## 细微：测试

- 测试 14 条：参数校验、HTML 提取器、DDG 解析器（含单条/多条/相对 URL）
- 全量 310 条通过
