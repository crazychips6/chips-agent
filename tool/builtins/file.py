"""file 工具 — 文件读写

路径安全校验内联实现，不违反 tool/ 零依赖约束。
使用 Path.resolve() 跟随符号链接并归一化路径，防止绕过。"""

import fnmatch
from pathlib import Path

from tool.registry import registry


# ── 敏感路径匹配 ──

_SENSITIVE_FILE_PATTERNS: list[str] = [
    ".env",
    ".env.*",
    "*.pem",
    "*id_rsa*",
    "*id_ecdsa*",
    "*_rsa",
    "*_dsa",
    "*_ed25519",
    "*.key",
    ".chips/",
    ".memory/",
]

_SENSITIVE_WRITE_PATHS: list[str] = [
    "/etc/",
    "/usr/",
    "/bin/",
    "/sbin/",
    "/boot/",
    "/dev/",
    "/proc/",
    "/sys/",
    "/var/",
    ".git/",
    ".chips/",
    ".memory/",
]


def _is_sensitive_path(resolved: Path) -> str | None:
    """检查真实路径是否敏感，匹配目录名或文件名。"""
    parts = resolved.parts
    for pattern in _SENSITIVE_FILE_PATTERNS:
        if pattern.endswith("/"):
            if pattern.rstrip("/") in parts:
                return f"拒绝访问敏感路径（匹配模式：{pattern}）"
        elif "*" in pattern or "?" in pattern:
            if fnmatch.fnmatch(resolved.name, pattern):
                return f"拒绝访问敏感文件（匹配模式：{pattern}）"
        else:
            if resolved.name == pattern:
                return f"拒绝访问敏感文件（匹配模式：{pattern}）"
    return None


def _check_write_path(resolved: Path) -> str | None:
    """检查写入路径是否安全。"""
    str_path = str(resolved)
    parts = resolved.parts
    for pattern in _SENSITIVE_WRITE_PATHS:
        stripped = pattern.rstrip("/")
        if stripped.startswith("/"):
            # 绝对路径：检查 resolved 是否以该路径开头
            prefix = stripped + "/"
            if str_path == stripped or str_path.startswith(prefix):
                return f"拒绝写入敏感路径（匹配模式：{pattern}）"
        else:
            # 相对路径组件：检查是否出现在路径段中
            if stripped in parts:
                return f"拒绝写入敏感路径（匹配模式：{pattern}）"
    return None


def _resolve_path(path: str) -> tuple[Path | None, str | None]:
    """解析路径为真实绝对路径（跟随符号链接）。"""
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError) as e:
        return None, f"路径解析错误：{e}"
    return resolved, None


# ── Handler ──


def _read_handler(args) -> str:
    path = args.get("path", "")
    if not path:
        return "错误：路径不能为空"

    resolved, err = _resolve_path(path)
    if err:
        return err

    reason = _is_sensitive_path(resolved)
    if reason:
        return reason

    if not resolved.is_file():
        return f"错误：文件不存在或不是普通文件：{path}"

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
        return content
    except PermissionError:
        return f"错误：无权限读取文件：{path}"
    except Exception as e:
        return f"错误：读取失败：{e}"


def _write_handler(args) -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    mode = args.get("mode", "write")

    if not path:
        return "错误：路径不能为空"

    resolved, err = _resolve_path(path)
    if err:
        return err

    reason = _check_write_path(resolved)
    if reason:
        return reason

    # 检查文件已存在且为敏感类型（写入覆盖检查）
    if resolved.exists():
        reason = _is_sensitive_path(resolved)
        if reason:
            return reason

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        flag = "a" if mode == "append" else "w"
        with open(resolved, flag, encoding="utf-8") as f:
            f.write(content)
        verb = "追加到" if mode == "append" else "写入"
        return f"已{verb} {resolved}（{len(content)} 字符）"
    except PermissionError:
        return f"错误：无权限写入文件：{path}"
    except Exception as e:
        return f"错误：写入失败：{e}"


# ── Register ──

registry.register(
    name="file_read",
    toolset="core",
    schema={
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "读取本地文件内容，返回文件文本。不能读取 .env、密钥文件等敏感文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对路径或绝对路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    handler=_read_handler,
)

registry.register(
    name="file_write",
    toolset="core",
    schema={
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "将内容写入本地文件。不允許写入 /etc/、.git/ 等敏感目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["write", "append"],
                        "description": "写入模式：write=覆盖, append=追加",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    handler=_write_handler,
)
