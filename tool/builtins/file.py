"""file 工具 — 文件读写与搜索

路径安全校验内联实现，不违反 tool/ 零依赖约束。
使用 Path.resolve() 跟随符号链接并归一化路径，防止绕过。"""

import fnmatch
import os
import re
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

# 搜索时跳过的目录
_SKIP_DIRS = {".chips", ".memory", ".git", "__pycache__", "node_modules", ".venv", ".tox", ".mypy_cache", ".pytest_cache"}


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
            prefix = stripped + "/"
            if str_path == stripped or str_path.startswith(prefix):
                return f"拒绝写入敏感路径（匹配模式：{pattern}）"
        else:
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


def _format_lines(lines: list[str], start_line: int = 1) -> str:
    """格式化为行号前缀输出。"""
    if not lines:
        return ""
    end_line = start_line + len(lines) - 1
    width = len(str(end_line))
    return "\n".join(f"{i + start_line:>{width}} | {line.rstrip()}" for i, line in enumerate(lines))


# ── Handlers ──


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
    except PermissionError:
        return f"错误：无权限读取文件：{path}"
    except Exception as e:
        return f"错误：读取失败：{e}"

    start_line = args.get("start_line")
    end_line = args.get("end_line")

    if start_line is None and end_line is None:
        return content

    lines = content.splitlines(keepends=False)
    total = len(lines)

    start = (start_line or 1) - 1
    if start < 0:
        start = 0
    if start >= total:
        return f"错误：start_line={start_line} 超出文件总行数 {total}"

    end = end_line if end_line is not None else total
    if end > total:
        end = total
    if end <= start:
        return f"错误：end_line={end_line} 必须大于 start_line={start_line}"

    selected = lines[start:end]
    return _format_lines(selected, start + 1)


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

    if resolved.exists():
        reason = _is_sensitive_path(resolved)
        if reason:
            return reason

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)

        if mode == "patch":
            search = args.get("search", "")
            replace = args.get("replace", "")
            if not search:
                return "错误：patch 模式需要 search 参数"

            current = resolved.read_text(encoding="utf-8", errors="replace")
            count = current.count(search)
            if count == 0:
                return f"错误：未找到匹配内容：{search[:80]}"
            new_content = current.replace(search, replace, 1)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(new_content)
            return f"已替换 1 处匹配（共 {count} 处）"

        flag = "a" if mode == "append" else "w"
        with open(resolved, flag, encoding="utf-8") as f:
            f.write(content)
        verb = "追加到" if mode == "append" else "写入"
        return f"已{verb} {resolved}（{len(content)} 字符）"
    except PermissionError:
        return f"错误：无权限写入文件：{path}"
    except Exception as e:
        return f"错误：写入失败：{e}"


def _search_handler(args) -> str:
    """在目录中搜索文本（类似 grep）。"""
    path = args.get("path", ".")
    pattern = args.get("pattern", "")
    pattern_type = args.get("pattern_type", "text")
    max_results = args.get("max_results", 50)

    if not pattern:
        return "错误：搜索模式不能为空"
    if not isinstance(max_results, int) or max_results < 1:
        max_results = 50

    resolved, err = _resolve_path(path)
    if err:
        return err

    if not resolved.exists():
        return f"错误：路径不存在：{path}"

    # 如果 path 指向文件，直接搜该文件
    if resolved.is_file():
        search_paths = [resolved]
    else:
        search_paths = None  # 表示需要遍历

    # 编译搜索模式
    try:
        if pattern_type == "regex":
            prog = re.compile(pattern)
            match_fn = lambda line: bool(prog.search(line))  # noqa: E731
        else:
            pattern_lower = pattern.lower()
            match_fn = lambda line: pattern_lower in line.lower()  # noqa: E731
    except re.error as e:
        return f"错误：无效的正则表达式：{e}"

    results: list[tuple[Path, int, str]] = []

    if search_paths:
        for fp in search_paths:
            _search_file(fp, pattern, match_fn, results, max_results)
    else:
        for root, dirs, files in os.walk(resolved):
            # 跳过敏感目录
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fname in files:
                fp = Path(root) / fname
                if _is_sensitive_path(fp):
                    continue
                _search_file(fp, pattern, match_fn, results, max_results)
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

    if not results:
        return f"未找到匹配 '{pattern}' 的内容"

    # 格式化输出
    lines_out = []
    for fp, lineno, text in results[:max_results]:
        # 尽量用相对路径
        try:
            show = fp.relative_to(Path.cwd())
        except ValueError:
            show = fp
        lines_out.append(f"{show}:{lineno}:{text[:120]}")

    total = len(results)
    suffix = f"\n... 以及更多结果（共 {total} 处）" if total > max_results else ""
    return "\n".join(lines_out) + suffix


def _search_file(fp: Path, pattern: str, match_fn, results: list, max_results: int):
    """搜索单个文件，向 results 追加匹配行。"""
    if len(results) >= max_results:
        return
    try:
        content = fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    lines = content.splitlines()
    for lineno, line in enumerate(lines, 1):
        if match_fn(line):
            results.append((fp, lineno, line.strip()[:120]))
            if len(results) >= max_results:
                return


# ── Register ──

registry.register(
    name="file_read",
    toolset="file",
    schema={
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "读取本地文件内容，返回文件文本。支持行范围读取。不能读取 .env、密钥文件等敏感文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对路径或绝对路径",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（可选，从 1 开始）。指定后返回行号前缀的输出格式",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（可选，包含），需配合 start_line 使用",
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
    toolset="file",
    schema={
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "将内容写入本地文件。支持 write（覆盖）、append（追加）、patch（搜索替换）三种模式。不允许写入 /etc/、.git/ 等敏感目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容（patch 模式下无需此参数）",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["write", "append", "patch"],
                        "description": "write=覆盖, append=追加, patch=搜索替换第一个匹配",
                    },
                    "search": {
                        "type": "string",
                        "description": "patch 模式下要搜索的文本",
                    },
                    "replace": {
                        "type": "string",
                        "description": "patch 模式的替换文本",
                    },
                },
                "required": ["path"],
            },
        },
    },
    handler=_write_handler,
)

registry.register(
    name="file_search",
    toolset="file",
    schema={
        "type": "function",
        "function": {
            "name": "file_search",
            "description": "在指定目录中搜索包含指定文本的文件（类似 grep）。返回匹配的文件路径、行号和内容片段。跳过 .git/、node_modules/ 等目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "搜索的目录或文件路径，默认当前目录",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "搜索模式（text 模式不区分大小写，regex 模式区分大小写）",
                    },
                    "pattern_type": {
                        "type": "string",
                        "enum": ["text", "regex"],
                        "description": "搜索类型：text=纯文本匹配（默认，不区分大小写），regex=正则表达式",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数，默认 50",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    handler=_search_handler,
)

