"""file 工具 — 文件读写

模块级 _environment 引用由 cli.py wiring 时注入。
工具本身不依赖其他项目模块。
路径安全校验内联实现，不违反 tool/ 零依赖约束。"""

import os
import json

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

# .env 加不加都危险，放编译后的匹配
_SENSITIVE_PARTS = [s for s in _SENSITIVE_FILE_PATTERNS]

_wiring: dict = {"env": None}


def _is_sensitive_path(abspath: str) -> str | None:
    """检查路径是否敏感，返回原因或 None。"""
    # 检查文件名/路径是否匹配敏感模式
    for pattern in _SENSITIVE_FILE_PATTERNS:
        if pattern.endswith("/"):
            # 目录匹配
            if pattern.rstrip("/") in abspath.replace("\\", "/").split("/"):
                return f"拒绝访问敏感路径（匹配模式：{pattern}）"
        elif "*" in pattern:
            # glob 模式简化匹配
            prefix = pattern.replace("*", "")
            fn = os.path.basename(abspath)
            if prefix in fn:
                return f"拒绝访问敏感文件（匹配模式：{pattern}）"
        else:
            if pattern in abspath:
                return f"拒绝访问敏感文件（匹配模式：{pattern}）"
    return None


def _check_write_path(abspath: str) -> str | None:
    """检查写入路径是否安全。"""
    norm = os.path.normpath(abspath).replace("\\", "/")
    for pattern in _SENSITIVE_WRITE_PATHS:
        stripped = pattern.rstrip("/")
        if norm.startswith(stripped) or stripped in norm.split("/"):
            return f"拒绝写入敏感路径（匹配模式：{pattern}）"
    return None


def _resolve_path(path: str) -> tuple[str | None, str | None]:
    """解析路径为绝对路径，返回 (abspath, error)。"""
    try:
        abspath = os.path.abspath(path)
    except Exception as e:
        return None, f"路径解析错误：{e}"
    return abspath, None


# ── Handler ──


def _read_handler(args) -> str:
    path = args.get("path", "")
    if not path:
        return "错误：路径不能为空"

    abspath, err = _resolve_path(path)
    if err:
        return err

    reason = _is_sensitive_path(abspath)
    if reason:
        return reason

    if not os.path.isfile(abspath):
        return f"错误：文件不存在或不是普通文件：{path}"

    try:
        with open(abspath, encoding="utf-8", errors="replace") as f:
            content = f.read()
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

    abspath, err = _resolve_path(path)
    if err:
        return err

    reason = _check_write_path(abspath)
    if reason:
        return reason

    # 检查文件已存在且为敏感类型（写入覆盖检查）
    if os.path.exists(abspath):
        reason = _is_sensitive_path(abspath)
        if reason:
            return reason

    try:
        os.makedirs(os.path.dirname(abspath) or ".", exist_ok=True)
        flag = "a" if mode == "append" else "w"
        with open(abspath, flag, encoding="utf-8") as f:
            f.write(content)
        verb = "追加到" if mode == "append" else "写入"
        return f"已{verb} {os.path.relpath(abspath)}（{len(content)} 字符）"
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
