"""document 工具 — 文档解析与 OCR

支持格式：PDF, DOCX, DOC, TXT, MD, EML, MSG, PNG/JPG/TIFF(OCR)
依赖：pymupdf4llm, python-docx, chardet, pytesseract, Pillow, extract-msg
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tool.registry import registry

# ── 常量 ──

_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

_SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".rst",
    ".eml", ".msg",
    ".png", ".jpg", ".jpeg", ".tiff", ".bmp",
}

_SENSITIVE_DIRS = ("/etc", "/proc", "/sys", "/dev", "/root")

# Magic bytes 签名 → 预期扩展名
_MAGIC_SIGNATURES = {
    b"%PDF": ".pdf",
    b"PK\x03\x04": ".docx",
    b"\xd0\xcf\x11\xe0": ".doc",
    b"\x89PNG": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"RIFF": ".bmp",
    b"From ": ".eml",
    b"MZ": ".exe",  # Windows 可执行文件
}


# ── 安全校验 ──


def _check_magic_bytes(path: Path, expected_ext: str) -> str | None:
    """校验文件真实类型是否与扩展名匹配。"""
    try:
        with open(path, "rb") as f:
            header = f.read(16)
    except OSError:
        return None
    for magic, real_ext in _MAGIC_SIGNATURES.items():
        if header.startswith(magic) and real_ext != expected_ext:
            return f"错误：文件类型不匹配：扩展名 {expected_ext} 但实际是 {real_ext}"
    return None


def _validate_path(path: str) -> tuple[Path | None, str | None]:
    """统一路径校验：符号链接追踪、敏感目录黑名单、扩展名白名单、大小限制、magic bytes。"""
    if not path:
        return None, "错误：路径不能为空"

    # 1. 路径解析 + 符号链接追踪
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError) as e:
        return None, f"路径解析错误：{e}"

    # 2. 敏感目录黑名单
    if any(str(resolved).startswith(d) for d in _SENSITIVE_DIRS):
        return None, "错误：路径越界：不允许访问系统目录"

    # 3. 文件存在性
    if not resolved.is_file():
        return None, f"错误：文件不存在：{path}"

    # 4. 扩展名白名单
    ext = resolved.suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        return None, f"错误：不支持的文件类型 {ext}（支持：{', '.join(sorted(_SUPPORTED_EXTENSIONS))}）"

    # 5. 空文件检查
    try:
        size = resolved.stat().st_size
    except OSError as e:
        return None, f"错误：无法读取文件信息：{e}"

    if size == 0:
        return None, "错误：文件为空（0 字节）"

    # 6. 大小限制
    if size > _MAX_FILE_SIZE:
        return None, f"错误：文件过大（{size / 1024 / 1024:.1f}MB），限制 {_MAX_FILE_SIZE / 1024 / 1024:.0f}MB"

    # 7. Magic bytes 校验
    magic_err = _check_magic_bytes(resolved, ext)
    if magic_err:
        return None, magic_err

    return resolved, None


# ── 文件大小格式化 ──


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


# ── 解析实现 ──


def _read_pdf(path: Path) -> str:
    """PDF → Markdown（pymupdf4llm）。"""
    try:
        import pymupdf4llm
        return pymupdf4llm.to_markdown(str(path))
    except Exception as e:
        return f"错误：文件损坏，无法解析 PDF：{e}"


def _read_docx(path: Path) -> str:
    """DOCX → 纯文本 + 表格。"""
    try:
        from docx import Document
        doc = Document(str(path))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                parts.append(" | ".join(cells))
        return "\n".join(parts) if parts else "（DOCX 无文本内容）"
    except Exception as e:
        return f"错误：文件损坏，无法解析 DOCX：{e}"


def _read_doc(path: Path) -> str:
    """旧版 DOC（OLE2 格式）→ 提取文本。"""
    try:
        import subprocess
        result = subprocess.run(
            ["antiword", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        # antiword 不可用时尝试 catdoc
        result = subprocess.run(
            ["catdoc", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        return "错误：无法解析旧版 DOC 格式（需要 antiword 或 catdoc）"
    except FileNotFoundError:
        return "错误：无法解析旧版 DOC 格式（需要安装 antiword 或 catdoc）"
    except Exception as e:
        return f"错误：DOC 解析失败：{e}"


def _read_text(path: Path) -> str:
    """TXT/MD/RST → chardet 编码检测 + 回退链。"""
    try:
        raw = path.read_bytes()
    except Exception as e:
        return f"错误：文件读取失败：{e}"

    # 尝试 chardet（可选依赖）
    try:
        import chardet
        detected = chardet.detect(raw)
        encoding = detected.get("encoding") or "utf-8"
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    except ImportError:
        pass

    # 回退链：utf-8 → gbk → latin-1
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue

    # 最终回退
    return raw.decode("utf-8", errors="replace")


def _read_eml(path: Path) -> str:
    """EML → 邮件内容（email 标准库）。"""
    try:
        from email import policy
        from email.parser import BytesParser
        with open(path, "rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)
        parts = []
        if msg["From"]:
            parts.append(f"From: {msg['From']}")
        if msg["To"]:
            parts.append(f"To: {msg['To']}")
        if msg["Subject"]:
            parts.append(f"Subject: {msg['Subject']}")
        if msg["Date"]:
            parts.append(f"Date: {msg['Date']}")
        parts.append("")
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                parts.append(part.get_content())
        return "\n".join(parts)
    except Exception as e:
        return f"错误：EML 解析失败：{e}"


def _read_msg(path: Path) -> str:
    """MSG → 邮件内容（extract-msg）。"""
    try:
        import extract_msg
        msg = extract_msg.Message(str(path))
        parts = []
        if msg.sender:
            parts.append(f"From: {msg.sender}")
        if msg.to:
            parts.append(f"To: {msg.to}")
        if msg.subject:
            parts.append(f"Subject: {msg.subject}")
        parts.append("")
        parts.append(msg.body or "（无正文）")
        return "\n".join(parts)
    except Exception as e:
        return f"错误：MSG 解析失败：{e}"


def _ocr_image(path: Path, lang: str = "chi_sim+eng") -> str:
    """图片 OCR 文字识别。"""
    try:
        from PIL import Image
        import pytesseract
        img = Image.open(path)
        text = pytesseract.image_to_string(img, lang=lang)
        return text.strip() if text and text.strip() else "（OCR 未识别到文字）"
    except ImportError:
        return "错误：OCR 需要安装 pytesseract 和 Pillow"
    except Exception as e:
        return f"错误：OCR 失败：{e}"


# ── Action handlers ──


def _handle_read(args: dict) -> str:
    """提取文档文本内容。"""
    path = args.get("path", "")
    resolved, err = _validate_path(path)
    if err:
        return err

    ext = resolved.suffix.lower()
    reader_map = {
        ".pdf": _read_pdf,
        ".docx": _read_docx,
        ".doc": _read_doc,
        ".txt": _read_text,
        ".md": _read_text,
        ".rst": _read_text,
        ".eml": _read_eml,
        ".msg": _read_msg,
        ".png": lambda p: _ocr_image(p, args.get("lang", "chi_sim+eng")),
        ".jpg": lambda p: _ocr_image(p, args.get("lang", "chi_sim+eng")),
        ".jpeg": lambda p: _ocr_image(p, args.get("lang", "chi_sim+eng")),
        ".tiff": lambda p: _ocr_image(p, args.get("lang", "chi_sim+eng")),
        ".bmp": lambda p: _ocr_image(p, args.get("lang", "chi_sim+eng")),
    }
    reader = reader_map.get(ext)
    if reader:
        return reader(resolved)
    return f"错误：不支持的文件类型 {ext}"


def _handle_info(args: dict) -> str:
    """获取文档元信息。"""
    path = args.get("path", "")
    resolved, err = _validate_path(path)
    if err:
        return err

    ext = resolved.suffix.lower()
    stat = resolved.stat()
    info = {
        "path": str(resolved),
        "name": resolved.name,
        "extension": ext,
        "size_bytes": stat.st_size,
        "size_human": _human_size(stat.st_size),
    }

    try:
        if ext == ".pdf":
            import pymupdf4llm
            import pymupdf
            doc = pymupdf.open(str(resolved))
            info["pages"] = len(doc)
            info["metadata"] = doc.metadata or {}
            doc.close()
        elif ext == ".docx":
            from docx import Document
            doc = Document(str(resolved))
            info["paragraphs"] = len(doc.paragraphs)
            info["tables"] = len(doc.tables)
        elif ext == ".eml":
            from email import policy
            from email.parser import BytesParser
            with open(resolved, "rb") as f:
                msg = BytesParser(policy=policy.default).parse(f)
            info["from"] = msg["From"] or ""
            info["to"] = msg["To"] or ""
            info["subject"] = msg["Subject"] or ""
            info["date"] = msg["Date"] or ""
        elif ext == ".msg":
            import extract_msg
            msg = extract_msg.Message(str(resolved))
            info["from"] = msg.sender or ""
            info["to"] = msg.to or ""
            info["subject"] = msg.subject or ""
    except Exception as e:
        info["meta_error"] = str(e)

    return json.dumps(info, ensure_ascii=False, indent=2)


def _handle_ocr(args: dict) -> str:
    """图片 OCR。"""
    path = args.get("path", "")
    resolved, err = _validate_path(path)
    if err:
        return err

    ext = resolved.suffix.lower()
    image_exts = {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
    if ext not in image_exts:
        return f"错误：OCR 仅支持图片文件，当前为 {ext}"

    lang = args.get("lang", "chi_sim+eng")
    return _ocr_image(resolved, lang)


def _handle_convert(args: dict) -> str:
    """PDF → Markdown（pymupdf4llm 专用）。"""
    path = args.get("path", "")
    resolved, err = _validate_path(path)
    if err:
        return err

    ext = resolved.suffix.lower()
    if ext != ".pdf":
        return f"错误：convert 仅支持 PDF 文件，当前为 {ext}"

    try:
        import pymupdf4llm
        return pymupdf4llm.to_markdown(str(resolved))
    except Exception as e:
        return f"错误：文件损坏，无法转换 PDF：{e}"


# ── 主分发 ──


def _handle(args: dict) -> str:
    action = args.get("action", "")
    handlers = {
        "read": _handle_read,
        "info": _handle_info,
        "ocr": _handle_ocr,
        "convert": _handle_convert,
    }
    handler = handlers.get(action)
    if handler:
        return handler(args)
    return json.dumps({"error": f"未知操作: {action}（支持: read, info, ocr, convert）"})


# ── Schema ──

DOCUMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "document",
        "description": "文档解析与 OCR（支持 PDF/DOCX/TXT/EML/MSG/图片）",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "info", "ocr", "convert"],
                    "description": "操作类型：read=提取文本, info=元信息, ocr=图片文字识别, convert=PDF转Markdown",
                },
                "path": {
                    "type": "string",
                    "description": "文件路径",
                },
                "lang": {
                    "type": "string",
                    "description": "OCR 语言（默认 chi_sim+eng），可选：eng, chi_sim, chi_tra 等",
                },
            },
            "required": ["action", "path"],
        },
    },
}


# ── 依赖检查 ──


def _check_dependencies() -> bool:
    """检查核心依赖是否可用。"""
    try:
        import pymupdf4llm
        from docx import Document
        return True
    except ImportError:
        return False


# ── 注册 ──

registry.register(
    name="document",
    toolset="document",
    schema=DOCUMENT_SCHEMA,
    handler=_handle,
    check_fn=_check_dependencies,
    group="dev",
    model_scope="large",
)
