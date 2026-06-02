"""凭证剥离与日志脱敏

提供：
- DEFAULT_ENV_BLOCKLIST：默认敏感环境变量列表
- strip_env()：过滤环境变量中敏感条目
- redact()：文本脱敏
- RedactingFormatter：logging 日志脱敏格式化器

零项目内部依赖。"""

import os
import re
import logging


# ── 环境变量黑名单 ──

DEFAULT_ENV_BLOCKLIST = frozenset({
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "SLACK_TOKEN",
    "DISCORD_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "DB_PASSWORD",
    "DATABASE_URL",
    "REDIS_URL",
    "SECRET_KEY",
    "PRIVATE_KEY",
    "ACCESS_TOKEN",
    "API_KEY",
    "PASSWORD",
    "SECRET",
})


def strip_env(env: dict[str, str] | None = None,
              blocklist: set[str] | None = None) -> dict[str, str]:
    """过滤环境变量，将敏感值替换为 '***'。

    Args:
        env: 要过滤的环境变量 dict，默认使用 os.environ。
        blocklist: 敏感键名集合，默认使用 DEFAULT_ENV_BLOCKLIST。

    Returns:
        过滤后的新 dict（不修改原对象）。
    """
    if env is None:
        env = dict(os.environ)
    env = dict(env)

    block_upper = {b.upper() for b in (blocklist or DEFAULT_ENV_BLOCKLIST)}
    for key in list(env):
        if key.upper() in block_upper:
            env[key] = "***"
    return env


# ── 文本脱敏 ──

_SENSITIVE_PATTERNS = [
    re.compile(r'(sk-)[a-zA-Z0-9]{20,}'),
    re.compile(r'(ghp_)[a-zA-Z0-9]{36,}'),
    re.compile(r'(gho_)[a-zA-Z0-9]{36,}'),
    re.compile(r'(xox[bpras]-)[a-zA-Z0-9-]{10,}'),
    re.compile(r'(Bearer\s+)[a-zA-Z0-9\-_\.]{20,}'),
]


def redact(text: str) -> str:
    """将文本中的密钥/令牌替换为 '***'。"""
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(r'\1***', text)
    return text


class RedactingFormatter(logging.Formatter):
    """自动脱敏的日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
