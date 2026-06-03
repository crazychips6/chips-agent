## 重要：Phase 10 C — 路径安全重写

重写 `tool/builtins/file.py` 的路径安全校验。

### 改动

**1. `os.path.abspath` → `Path(path).resolve()`**
- 解析符号链接，阻止 symlink 绕过
- 追踪 `..` 穿越，归一化为真实路径

**2. `pattern in abspath` 字符串匹配 → `Path.parts` + `fnmatch`**
- 文件名精确匹配：`resolved.name == pattern`（如 `.env` 只匹配文件名恰为 `.env`）
- Glob 模式：`fnmatch.fnmatch(resolved.name, pattern)`（如 `*.pem`、`*id_rsa*`）
- 目录匹配：`pattern in resolved.parts`（如 `.chips` 匹配路径段，不误伤 `env.txt`）

**3. `_check_write_path` 区分绝对/相对路径**
- 绝对路径用 `str(resolved).startswith(prefix)`（如 `/etc/` 匹配 `/etc/evil.conf`）
- 相对路径用 `stripped in resolved.parts`（如 `.git` 匹配 `.git/HEAD`）

### 测试

- 新增 4 个测试：symlink 到 .env 被拦截、.. 穿越解析、非 .env 不误杀、/etc 前缀不误伤 /etcetera
- 全量测试从 228 → 232 通过
