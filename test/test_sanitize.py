"""safety/sanitize 模块测试

全覆盖 strip_env、redact、RedactingFormatter。"""

import logging

from safety.sanitize import (
    strip_env,
    redact,
    RedactingFormatter,
    DEFAULT_ENV_BLOCKLIST,
)


class TestStripEnv:
    """环境变量剥离。"""

    def test_strip_known_secrets(self):
        env = {
            "DEEPSEEK_API_KEY": "sk-abc123",
            "PATH": "/usr/bin",
            "HOME": "/home/user",
        }
        result = strip_env(env)
        assert result["DEEPSEEK_API_KEY"] == "***"
        assert result["PATH"] == "/usr/bin"
        assert result["HOME"] == "/home/user"

    def test_default_env_contains_secrets(self):
        """真实 os.environ 中的密钥应被脱敏。"""
        import os

        env = dict(os.environ)
        # 注入一个测试密钥
        env["TEST_FAKE_API_KEY"] = "sk-fake-test-key-12345"
        result = strip_env(env, blocklist={"TEST_FAKE_API_KEY"})
        assert result["TEST_FAKE_API_KEY"] == "***"

    def test_case_insensitive(self):
        env = {"deepseek_api_key": "sk-test", "Path": "/bin"}
        result = strip_env(env)
        assert result["deepseek_api_key"] == "***"
        assert result["Path"] == "/bin"

    def test_custom_blocklist(self):
        env = {"MY_SECRET": "sensitive", "NORMAL": "ok"}
        result = strip_env(env, blocklist={"MY_SECRET"})
        assert result["MY_SECRET"] == "***"
        assert result["NORMAL"] == "ok"

    def test_no_mutation_of_original(self):
        original = {"KEY": "val"}
        result = strip_env(original, blocklist={"KEY"})
        assert result["KEY"] == "***"
        assert original["KEY"] == "val"

    def test_empty_env(self):
        result = strip_env({})
        assert result == {}


class TestRedact:
    """文本脱敏。"""

    def test_openai_key(self):
        text = "使用密钥 sk-abcdefghijklmnopqrstuvwxyz123456"
        result = redact(text)
        assert result == "使用密钥 sk-***"

    def test_github_pat(self):
        text = "token: ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        result = redact(text)
        assert result == "token: ghp_***"

    def test_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        result = redact(text)
        assert result == "Authorization: Bearer ***"

    def test_no_false_positive(self):
        text = "普通文本，不含密钥"
        result = redact(text)
        assert result == text

    def test_empty_string(self):
        assert redact("") == ""

    def test_multiple_sensitive_values(self):
        text = "key1=sk-abc1234567890123456789 key2=sk-xyz9876543210987654321"
        result = redact(text)
        assert result == "key1=sk-*** key2=sk-***"


class TestRedactingFormatter:
    """日志脱敏格式化器。"""

    def test_formatter_redacts(self):
        fmt = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="key is sk-abcdefghijklmnopqrstuvwxyz123456",
            args=None,
            exc_info=None,
        )
        output = fmt.format(record)
        assert output == "key is sk-***"

    def test_formatter_normal_message(self):
        fmt = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello world",
            args=None,
            exc_info=None,
        )
        assert fmt.format(record) == "hello world"
