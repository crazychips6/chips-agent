"""document 工具单元测试 — 安全机制 + 功能验证"""

import json
import os
import tempfile
from pathlib import Path

import pytest


# ── 导入被测模块 ──

from tool.builtins.document import (
    _validate_path,
    _check_magic_bytes,
    _read_text,
    _read_pdf,
    _read_docx,
    _handle,
    _MAGIC_SIGNATURES,
    _SUPPORTED_EXTENSIONS,
    _MAX_FILE_SIZE,
)


# ── 安全机制测试 ──


class TestValidatePath:
    """_validate_path 安全校验。"""

    def test_empty_path(self):
        _, err = _validate_path("")
        assert err is not None
        assert "路径不能为空" in err

    def test_nonexistent_file(self):
        _, err = _validate_path("/tmp/nonexistent_file_xyz_123.txt")
        assert err is not None
        assert "文件不存在" in err

    def test_unsupported_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(b"test content")
            path = f.name
        try:
            _, err = _validate_path(path)
            assert err is not None
            assert "不支持的文件类型" in err
        finally:
            os.unlink(path)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = f.name
        try:
            _, err = _validate_path(path)
            assert err is not None
            assert "文件为空" in err
        finally:
            os.unlink(path)

    def test_valid_text_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            f.write(b"Hello, world!")
            path = f.name
        try:
            resolved, err = _validate_path(path)
            assert err is None
            assert resolved is not None
            assert resolved.suffix == ".txt"
        finally:
            os.unlink(path)

    def test_sensitive_dir_block(self):
        _, err = _validate_path("/etc/passwd")
        assert err is not None
        assert "路径越界" in err

    def test_symlink_resolve(self):
        """符号链接会被 resolve() 追踪到真实路径。"""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as target:
            target.write(b"target content")
            target_path = target.name

        link_path = target_path + ".link"
        try:
            os.symlink(target_path, link_path)
            resolved, err = _validate_path(link_path)
            assert err is None
            assert resolved == Path(target_path)
        finally:
            os.unlink(link_path)
            os.unlink(target_path)


class TestMagicBytes:
    """Magic bytes 校验。"""

    def test_pdf_matches(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, mode="wb") as f:
            f.write(b"%PDF-1.4 some content")
            path = f.name
        try:
            result = _check_magic_bytes(Path(path), ".pdf")
            assert result is None  # 匹配，无错误
        finally:
            os.unlink(path)

    def test_mismatch_pdf_is_exe(self):
        """扩展名 .pdf 但实际是 exe。"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, mode="wb") as f:
            f.write(b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff")
            path = f.name
        try:
            result = _check_magic_bytes(Path(path), ".pdf")
            assert result is not None
            assert "文件类型不匹配" in result
        finally:
            os.unlink(path)

    def test_docx_matches(self):
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False, mode="wb") as f:
            f.write(b"PK\x03\x04\x14\x00\x06\x00")
            path = f.name
        try:
            result = _check_magic_bytes(Path(path), ".docx")
            assert result is None
        finally:
            os.unlink(path)


# ── 文本读取测试 ──


class TestReadText:
    """TXT chardet 编码检测。"""

    def test_utf8_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            f.write("你好世界".encode("utf-8"))
            path = f.name
        try:
            content = _read_text(Path(path))
            assert "你好世界" in content
        finally:
            os.unlink(path)

    def test_gbk_file(self):
        """GBK 编码文件应被 chardet 检测并正确解码。"""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            f.write("你好世界".encode("gbk"))
            path = f.name
        try:
            content = _read_text(Path(path))
            assert "你好世界" in content
        finally:
            os.unlink(path)


# ── Action 分发测试 ──


class TestHandle:
    """_handle action 分发。"""

    def test_unknown_action(self):
        result = _handle({"action": "unknown", "path": "/tmp/test.txt"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "未知操作" in parsed["error"]

    def test_read_nonexistent(self):
        result = _handle({"action": "read", "path": "/tmp/nonexistent_xyz.txt"})
        assert "错误" in result
        assert "文件不存在" in result

    def test_read_valid_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            f.write("测试内容".encode("utf-8"))
            path = f.name
        try:
            result = _handle({"action": "read", "path": path})
            assert "测试内容" in result
        finally:
            os.unlink(path)

    def test_info_nonexistent(self):
        result = _handle({"action": "info", "path": "/tmp/nonexistent_xyz.txt"})
        assert "错误" in result

    def test_convert_non_pdf(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            f.write(b"test content")
            path = f.name
        try:
            result = _handle({"action": "convert", "path": path})
            assert "错误" in result
            assert "仅支持 PDF" in result
        finally:
            os.unlink(path)


# ── 依赖检查测试 ──


class TestDependencies:
    """check_fn 依赖检查。"""

    def test_check_fn_callable(self):
        from tool.builtins.document import _check_dependencies
        # 无论依赖是否安装，check_fn 应返回 bool
        result = _check_dependencies()
        assert isinstance(result, bool)
