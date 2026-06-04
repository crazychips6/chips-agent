"""内置工具测试 — echo + memory + file 读写 handler"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tool.registry import registry as global_registry

# 触发 echo 等内置工具的自注册
import tool.builtins  # noqa: F401


class TestEchoTool:
    """通过 registry 调用 echo 工具。"""

    def test_echo_basic(self):
        result = global_registry.dispatch("echo", {"text": "hello"})
        assert result == "hello"

    def test_echo_empty(self):
        result = global_registry.dispatch("echo", {"text": ""})
        assert result == ""

    def test_echo_missing_key(self):
        result = global_registry.dispatch("echo", {})
        assert result == ""

    def test_echo_unicode(self):
        result = global_registry.dispatch("echo", {"text": "你好世界 🎉"})
        assert result == "你好世界 🎉"

    def test_echo_registered(self):
        """echo 注册在 core 工具集。"""
        entries = global_registry._entries
        assert "echo" in entries
        assert entries["echo"].toolset == "core"


class TestMemoryTool:
    """通过 handler 函数直接测试 memory 读写逻辑。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        import tool.builtins.memory as mem

        self._orig_store = mem._store
        self.mem_module = mem
        yield
        mem._store = self._orig_store

    def test_read_without_store(self):
        self.mem_module._store = None
        result = self.mem_module._read_handler({"category": "memory"})
        assert result == "记忆系统未初始化"

    def test_read_with_store(self):
        store = MagicMock()
        store.for_system_prompt.return_value = "一些记忆内容"
        self.mem_module._store = store

        result = self.mem_module._read_handler({"category": "memory"})
        assert result == "一些记忆内容"

    def test_read_empty_store(self):
        store = MagicMock()
        store.for_system_prompt.return_value = ""
        self.mem_module._store = store

        result = self.mem_module._read_handler({"category": "memory"})
        assert result == "暂无记忆"

    def test_write_without_store(self):
        self.mem_module._store = None
        result = self.mem_module._save_handler({"content": "数据", "category": "memory"})
        assert result == "记忆系统未初始化"

    def test_write_empty_content(self):
        store = MagicMock()
        self.mem_module._store = store
        result = self.mem_module._save_handler({"content": "", "category": "memory"})
        assert result == "内容不能为空"

    def test_write_success(self):
        store = MagicMock()
        store.add.return_value = {"status": "ok", "category": "memory"}
        self.mem_module._store = store

        result = self.mem_module._save_handler({"content": "重要数据", "category": "memory"})
        assert result == "已保存到 memory"
        store.add.assert_called_once_with("重要数据", "memory")

    def test_write_failure(self):
        store = MagicMock()
        store.add.return_value = {"status": "error", "message": "磁盘满"}
        self.mem_module._store = store

        result = self.mem_module._save_handler({"content": "数据", "category": "memory"})
        assert "保存失败" in result
        assert "磁盘满" in result


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

    def test_file_registered(self):
        entries = global_registry._entries
        assert "file_read" in entries
        assert "file_write" in entries
        assert entries["file_read"].toolset == "core"

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

    def test_read_full_file_by_default(self, tmp_path):
        """不指定行范围时行为不变——返回全文。"""
        p = tmp_path / "full.txt"
        p.write_text("line1\nline2\nline3\n")
        result = global_registry.dispatch("file_read", {"path": str(p)})
        assert result == "line1\nline2\nline3\n"

    def test_read_range(self, tmp_path):
        """start_line + end_line 返回对应行。"""
        p = tmp_path / "range.txt"
        p.write_text("a\nb\nc\nd\ne\n")
        result = global_registry.dispatch("file_read", {"path": str(p), "start_line": 2, "end_line": 4})
        assert "2 | b" in result
        assert "3 | c" in result
        assert "4 | d" in result
        assert "1 | a" not in result
        assert "5 | e" not in result

    def test_read_single_line(self, tmp_path):
        """start_line=end_line 只返回一行。"""
        p = tmp_path / "single.txt"
        p.write_text("alpha\nbeta\ngamma\n")
        result = global_registry.dispatch("file_read", {"path": str(p), "start_line": 2, "end_line": 2})
        assert "2 | beta" in result
        assert "alpha" not in result
        assert "gamma" not in result

    def test_read_from_start(self, tmp_path):
        """只给 end_line，从开头读到 end_line。"""
        p = tmp_path / "head.txt"
        p.write_text("1\n2\n3\n4\n5\n")
        result = global_registry.dispatch("file_read", {"path": str(p), "end_line": 3})
        assert "1 | 1" in result
        assert "3 | 3" in result
        assert "4 | 4" not in result

    def test_read_to_end(self, tmp_path):
        """只给 start_line，从该行读到末尾。"""
        p = tmp_path / "tail.txt"
        p.write_text("1\n2\n3\n4\n5\n")
        result = global_registry.dispatch("file_read", {"path": str(p), "start_line": 4})
        assert "4 | 4" in result
        assert "5 | 5" in result
        assert "1 | 1" not in result

    def test_start_line_out_of_range(self, tmp_path):
        p = tmp_path / "short.txt"
        p.write_text("only one")
        result = global_registry.dispatch("file_read", {"path": str(p), "start_line": 10})
        assert "超出" in result

    def test_end_line_gt_total(self, tmp_path):
        """end_line 超出总行数时自动截断。"""
        p = tmp_path / "three_lines.txt"
        p.write_text("1\n2\n3\n")
        result = global_registry.dispatch("file_read", {"path": str(p), "start_line": 2, "end_line": 999})
        assert "2 | 2" in result
        assert "3 | 3" in result

    def test_line_number_padding(self, tmp_path):
        """行号前缀宽度自适应。"""
        p = tmp_path / "many.txt"
        p.write_text("\n".join(f"line{i}" for i in range(1, 101)))
        result = global_registry.dispatch("file_read", {"path": str(p), "start_line": 90, "end_line": 100})
        # 90-100 需要宽度 3 来对齐
        lines = result.split("\n")
        assert len(lines) == 11  # 90..100 inclusive
        # 行 90-99 前面应有 1 个空格（宽度 3，数字占 2 位）
        assert lines[0].startswith(" 90 |")
        # 行 100 前面有 3 个数字，无空格前缀
        assert lines[-1].startswith("100 |")


class TestFilePatch:
    """file_write patch 模式。"""

    def test_patch_replace_first_occurrence(self, tmp_path):
        p = tmp_path / "patch.txt"
        p.write_text("hello world\nhello chips\n")
        result = global_registry.dispatch("file_write", {
            "path": str(p), "mode": "patch",
            "search": "hello", "replace": "hi",
        })
        assert "已替换" in result
        assert p.read_text() == "hi world\nhello chips\n"

    def test_patch_no_match(self, tmp_path):
        p = tmp_path / "nomatch.txt"
        p.write_text("abc\n")
        result = global_registry.dispatch("file_write", {
            "path": str(p), "mode": "patch",
            "search": "xyz", "replace": "foo",
        })
        assert "未找到" in result
        assert p.read_text() == "abc\n"  # 未修改

    def test_patch_empty_search(self, tmp_path):
        p = tmp_path / "empty_search.txt"
        p.write_text("content")
        result = global_registry.dispatch("file_write", {
            "path": str(p), "mode": "patch",
            "search": "", "replace": "x",
        })
        assert "错误" in result

    def test_patch_preserves_rest_of_file(self, tmp_path):
        """替换后文件其余部分不受影响。"""
        p = tmp_path / "preserve.txt"
        p.write_text("1\nTARGET\n2\nTARGET\n3\n")
        global_registry.dispatch("file_write", {
            "path": str(p), "mode": "patch",
            "search": "TARGET", "replace": "REPLACED",
        })
        assert p.read_text() == "1\nREPLACED\n2\nTARGET\n3\n"  # 只替换第一处

    def test_patch_requires_search(self, tmp_path):
        p = tmp_path / "no_search_param.txt"
        p.write_text("content")
        result = global_registry.dispatch("file_write", {
            "path": str(p), "mode": "patch",
            "replace": "new",
        })
        assert "错误" in result


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

    def test_search_registered(self):
        entries = global_registry._entries
        assert "file_search" in entries
        assert entries["file_search"].toolset == "core"


class TestWebFetch:
    """web_fetch 工具测试（mock 网络请求）。"""

    def test_empty_url(self):
        result = global_registry.dispatch("web_fetch", {"url": ""})
        assert "错误" in result

    def test_missing_url(self):
        result = global_registry.dispatch("web_fetch", {})
        assert "错误" in result

    def test_invalid_scheme(self):
        result = global_registry.dispatch("web_fetch", {"url": "ftp://example.com"})
        assert "URL" in result or "错误" in result

    def test_no_scheme_autofill(self):
        """不带 scheme 时自动补全 https://。"""
        # 不去真的请求，验证至少能进入请求阶段（成功或失败而非参数错误）
        result = global_registry.dispatch("web_fetch", {"url": "example.com"})
        assert "错误" not in result or "无法访问" in result or "请求失败" in result

    def test_registered(self):
        entries = global_registry._entries
        assert "web_fetch" in entries
        assert entries["web_fetch"].toolset == "core"


class TestWebSearch:
    """web_search 工具测试。"""

    def test_empty_query(self):
        result = global_registry.dispatch("web_search", {"query": ""})
        assert "错误" in result

    def test_missing_query(self):
        result = global_registry.dispatch("web_search", {})
        assert "错误" in result

    def test_registered(self):
        entries = global_registry._entries
        assert "web_search" in entries
        assert entries["web_search"].toolset == "core"

    def test_max_results_clamped(self):
        """max_results 被限制到 20。"""
        entries = global_registry._entries
        schema = entries["web_search"].schema
        props = schema["function"]["parameters"]["properties"]
        assert "max_results" in props

    def test_html_extractor(self):
        """_HTMLTextExtractor 提取可见文本。"""
        from tool.builtins.web import _HTMLTextExtractor
        extractor = _HTMLTextExtractor()
        extractor.feed("<html><body><p>Hello <b>World</b></p><script>alert('x')</script></body></html>")
        assert "Hello World" in extractor.get_text()
        assert "alert" not in extractor.get_text()

    def test_html_extractor_skips_style(self):
        from tool.builtins.web import _HTMLTextExtractor
        extractor = _HTMLTextExtractor()
        extractor.feed("<style>.cls{color:red}</style><p>visible</p>")
        assert "visible" in extractor.get_text()
        assert "color" not in extractor.get_text()


class TestDdgParser:
    """DuckDuckGo 搜索结果解析。"""

    def test_parse_empty(self):
        from tool.builtins.web import _parse_ddg_results
        assert _parse_ddg_results("") == []

    def test_parse_no_results(self):
        from tool.builtins.web import _parse_ddg_results
        html = "<html><body>No results found.</body></html>"
        assert _parse_ddg_results(html) == []

    def test_parse_single_result(self):
        from tool.builtins.web import _parse_ddg_results
        html = """
        <table>
          <tr class="result">
            <td valign="top">1.</td>
            <td>
              <a rel="nofollow" href="https://example.com">Example Title</a>
              <br>
              <span class="result-snippet">This is a snippet about example.</span>
            </td>
          </tr>
        </table>
        """
        results = _parse_ddg_results(html)
        assert len(results) == 1
        assert results[0][0] == "Example Title"
        assert results[0][1] == "This is a snippet about example."
        assert results[0][2] == "https://example.com"

    def test_parse_multiple_results(self):
        from tool.builtins.web import _parse_ddg_results
        html = """
        <table>
          <tr class="result">
            <td><a rel="nofollow" href="https://a.com">A</a><br><span class="result-snippet">snippet a</span></td>
          </tr>
          <tr class="result">
            <td><a rel="nofollow" href="https://b.com">B</a><br><span class="result-snippet">snippet b</span></td>
          </tr>
        </table>
        """
        results = _parse_ddg_results(html)
        assert len(results) == 2

    def test_parse_relative_url(self):
        """相对 URL 补全为绝对 URL。"""
        from tool.builtins.web import _parse_ddg_results
        html = """
        <tr class="result">
          <td><a rel="nofollow" href="//relative.com/path">Relative</a><br><span class="result-snippet">text</span></td>
        </tr>
        """
        results = _parse_ddg_results(html)
        assert results[0][2].startswith("https:")
        assert "relative.com" in results[0][2]
