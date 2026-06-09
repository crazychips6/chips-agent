"""内置工具测试 — echo + memory + file 读写 handler"""

from pathlib import Path

import pytest

from tool.registry import registry as global_registry

# 触发 echo 等内置工具的自注册
import tool.builtins  # noqa: F401


class TestEchoTool:
    """通过 registry 调用 echo 工具。"""

    def test_echo_basic(self):
        assert global_registry.dispatch("echo", {"text": "hello"}) == "hello"
        assert global_registry.dispatch("echo", {"text": "你好 🎉"}) == "你好 🎉"

    def test_echo_empty(self):
        assert global_registry.dispatch("echo", {"text": ""}) == ""
        assert global_registry.dispatch("echo", {}) == ""


class TestFileTool:
    """通过 registry 调用 file_read / file_write 工具。"""

    def test_file_read_nonexistent(self):
        result = global_registry.dispatch("file_read", {"path": "/tmp/nonexistent_file_xyz_123"})
        assert "错误" in result

    def test_file_read_sensitive_env(self):
        result = global_registry.dispatch("file_read", {"path": ".env"})
        assert "拒绝访问" in result or "拒绝" in result

    def test_file_read_sensitive_dot_chips(self):
        result = global_registry.dispatch("file_read", {"path": ".chips/sessions.db"})
        assert "拒绝" in result

    def test_file_read_empty_path(self):
        result = global_registry.dispatch("file_read", {"path": ""})
        assert "错误" in result

    def test_file_read_and_write_roundtrip(self, tmp_path):
        p = tmp_path / "test.txt"
        result = global_registry.dispatch("file_write", {"path": str(p), "content": "hello chips"})
        assert "已写入" in result

        result = global_registry.dispatch("file_read", {"path": str(p)})
        assert result == "hello chips"

    def test_file_write_append(self, tmp_path):
        p = tmp_path / "append.txt"
        global_registry.dispatch("file_write", {"path": str(p), "content": "line1\n"})
        global_registry.dispatch("file_write", {"path": str(p), "content": "line2\n", "mode": "append"})
        result = global_registry.dispatch("file_read", {"path": str(p)})
        assert result == "line1\nline2\n"

    def test_file_write_sensitive_path(self):
        result = global_registry.dispatch("file_write", {"path": "/etc/evil.conf", "content": "bad"})
        assert "拒绝" in result

    def test_file_write_sensitive_git(self):
        result = global_registry.dispatch("file_write", {"path": ".git/HEAD", "content": "hack"})
        assert "拒绝" in result

    def test_symlink_to_env_is_blocked(self, tmp_path):
        """符号链接指向 .env 应被拦截。"""
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=key")
        link = tmp_path / "config"
        link.symlink_to(env_file)
        result = global_registry.dispatch("file_read", {"path": str(link)})
        assert "拒绝" in result

    def test_resolve_dot_dot(self):
        """Path.resolve() 正确处理 .. 穿越。"""
        result = global_registry.dispatch("file_read", {"path": "../" * 10 + "tmp"})
        # 穿越到 /tmp → 解析成功，但 /tmp 是目录不是文件
        assert "不是普通文件" in result or "不存在" in result

    def test_non_dot_env_allowed(self):
        """env.txt 不是 .env，不应被误杀。"""
        result = global_registry.dispatch("file_read", {"path": "env.txt"})
        assert "拒绝" not in result

    def test_etc_prefix_no_false_positive(self, tmp_path):
        """/etc 前缀不误伤 /etcetera。"""
        p = tmp_path / "etcetera.txt"
        p.write_text("safe")
        result = global_registry.dispatch("file_write", {"path": str(p), "content": "test"})
        # 不应因为路径包含 "etc" 就被拦截
        assert "拒绝" not in result


class TestFileReadLineRange:
    """文件行范围读取。"""

    def test_read_range(self, tmp_path):
        """start_line + end_line 返回对应行。"""
        p = tmp_path / "range.txt"
        p.write_text("a\nb\nc\nd\ne\n")
        result = global_registry.dispatch("file_read", {"path": str(p), "start_line": 2, "end_line": 4})
        assert "2 | b" in result
        assert "3 | c" in result
        assert "4 | d" in result
        assert "1 | a" not in result

    def test_read_single_line(self, tmp_path):
        p = tmp_path / "single.txt"
        p.write_text("alpha\nbeta\ngamma\n")
        result = global_registry.dispatch("file_read", {"path": str(p), "start_line": 2, "end_line": 2})
        assert "2 | beta" in result

    def test_read_to_end(self, tmp_path):
        """只给 start_line，从该行读到末尾。"""
        p = tmp_path / "tail.txt"
        p.write_text("1\n2\n3\n4\n5\n")
        result = global_registry.dispatch("file_read", {"path": str(p), "start_line": 4})
        assert "4 | 4" in result

    def test_line_number_padding(self, tmp_path):
        """行号前缀宽度自适应。"""
        p = tmp_path / "many.txt"
        p.write_text("\n".join(f"line{i}" for i in range(1, 101)))
        result = global_registry.dispatch("file_read", {"path": str(p), "start_line": 90, "end_line": 100})
        lines = result.split("\n")
        assert len(lines) == 11
        assert lines[0].startswith(" 90 |")
        assert lines[-1].startswith("100 |")


class TestFilePatch:
    """file_write patch 模式。"""

    def test_patch_replace_first_occurrence(self, tmp_path):
        """替换首处匹配，其余部分不受影响。"""
        p = tmp_path / "patch.txt"
        p.write_text("1\nTARGET\n2\nTARGET\n3\n")
        result = global_registry.dispatch("file_write", {
            "path": str(p), "mode": "patch",
            "search": "TARGET", "replace": "REPLACED",
        })
        assert "已替换" in result
        assert p.read_text() == "1\nREPLACED\n2\nTARGET\n3\n"

    def test_patch_no_match(self, tmp_path):
        p = tmp_path / "nomatch.txt"
        p.write_text("abc\n")
        result = global_registry.dispatch("file_write", {
            "path": str(p), "mode": "patch",
            "search": "xyz", "replace": "foo",
        })
        assert "未找到" in result
        assert p.read_text() == "abc\n"  # 未修改


class TestFileSearch:
    """file_search 工具。"""

    def test_search_text_in_file(self, tmp_path):
        f = tmp_path / "greeting.txt"
        f.write_text("hello world\nfoo bar\nHELLO again\n")
        result = global_registry.dispatch("file_search", {
            "path": str(tmp_path),
            "pattern": "hello",
        })
        assert "greeting.txt" in result
        assert "hello world" in result
        assert "HELLO again" in result  # 不区分大小写

    def test_search_exact_case(self, tmp_path):
        """text 模式不区分大小写。"""
        f = tmp_path / "case.txt"
        f.write_text("Hello\nWORLD\n")
        result = global_registry.dispatch("file_search", {
            "path": str(tmp_path),
            "pattern": "hello",
        })
        assert "Hello" in result

    def test_search_regex(self, tmp_path):
        f = tmp_path / "regex.txt"
        f.write_text("abc123\ndef456\nxyz\n")
        result = global_registry.dispatch("file_search", {
            "path": str(tmp_path),
            "pattern": r"\d{3}",
            "pattern_type": "regex",
        })
        assert "abc123" in result
        assert "def456" in result
        assert "xyz" not in result

    def test_search_no_match(self, tmp_path):
        result = global_registry.dispatch("file_search", {
            "path": str(tmp_path),
            "pattern": "nonexistent",
        })
        assert "未找到" in result

    def test_search_empty_pattern(self):
        result = global_registry.dispatch("file_search", {"pattern": ""})
        assert "错误" in result

    def test_search_bad_regex(self):
        result = global_registry.dispatch("file_search", {
            "pattern": r"[invalid",
            "pattern_type": "regex",
        })
        assert "错误" in result

    def test_search_nonexistent_path(self):
        result = global_registry.dispatch("file_search", {
            "path": "/nonexistent_dir_xyz_999",
            "pattern": "test",
        })
        assert "错误" in result or "不存在" in result

    def test_search_single_file(self, tmp_path):
        f = tmp_path / "target.txt"
        f.write_text("secret content")
        # 指向文件而非目录
        result = global_registry.dispatch("file_search", {
            "path": str(f),
            "pattern": "secret",
        })
        assert "target.txt" in result

    def test_search_skips_binary(self, tmp_path):
        """二进制文件不崩溃，被跳过。"""
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        result = global_registry.dispatch("file_search", {
            "path": str(tmp_path),
            "pattern": "test",
        })
        assert "未找到" in result

    def test_search_skips_sensitive(self, tmp_path):
        """搜索跳过 .git/ .chips/ 等目录。"""
        git = tmp_path / ".git"
        git.mkdir()
        (git / "config").write_text("sensitive data")
        result = global_registry.dispatch("file_search", {
            "path": str(tmp_path),
            "pattern": "sensitive",
        })
        assert "未找到" in result



class TestWebFetch:
    """web_fetch 工具测试。"""

    def test_invalid_scheme(self):
        result = global_registry.dispatch("web_fetch", {"url": "ftp://example.com"})
        assert "不支持的协议 ftp" in result

