## 重要：Phase 10 E1 — 文件操作增强

### file_read — 行范围读取

- 新增 `start_line` / `end_line` 可选参数，无参数时行为不变（全文返回）
- 支持四种组合：区间（start+end）、开头到行（end only）、行到尾（start only）、单行
- 输出带行号前缀，行号宽度自适应对齐：`行号 | 内容`
- 越界处理：start_line 超出 → 提示错误；end_line 超出 → 截断到文件末尾

### file_write — patch（搜索替换）模式

- 新增 `mode="patch"`，配合 `search` / `replace` 参数
- 仅替换第一个匹配，其余不动（安全和可预测）
- 找不到匹配时返回错误，不修改文件

## 普通：file_search — 新增文本搜索工具

- 类似 grep，支持 `text`（默认，不区分大小写）和 `regex` 两种搜索类型
- 自动跳过 `.git/`、`node_modules/`、`.venv/`、`__pycache__/` 等目录
- 跳过敏感文件（.env、密钥等）和二进制文件
- `max_results` 参数控制最大返回数（默认 50）
- 结果格式：`文件路径:行号:匹配行内容片段`

## 细微：测试

- `test_builtins.py` 新增 3 个测试类共 24 条：行范围（8）、patch（5）、搜索（11）
- 全量 294 条通过
