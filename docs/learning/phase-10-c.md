# Phase 10 C 学习笔记

## 路径安全重写

**核心改动：`os.path.abspath` → `Path(path).resolve()`**

- `os.path.abspath` 只做字符串拼接，不碰文件系统，符号链接和 `..` 可绕过
- `Path(path).resolve()` 调用内核 `realpath()`，沿路径逐段解析到磁盘真实路径，符号链接跟随

**模式匹配：`pattern in abspath` → `Path.parts` + `fnmatch`**

- 文件名精确匹配：`resolved.name == ".env"`，不再误杀 `env.txt`
- Glob 模式：`fnmatch("*.pem", resolved.name)`，正确处理通配符
- 目录匹配：`".git" in resolved.parts`，按路径段匹配不误伤 `/etcetera`
