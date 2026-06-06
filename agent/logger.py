"""结构化日志系统 — JSON 文件 + 控制台 + 模块路由

设计原则:
  - 所有日志走 chips.* logger 层级，不依赖 root logger
  - 模块使用 logging.getLogger("chips.<module>") 命名约定
  - 文件输出 JSON lines（每行一个对象，可被 filebeat/loki 等消费）
  - 控制台输出紧凑格式（人类可读，stderr）
  - 前缀过滤器实现模块分流：chips.tool.* → tool.log
  - LogManager 单例，支持 setup() 多次调用重建
  - 脱敏集成（safety.sanitize.redact）

用法:
    from agent.logger import setup_logging, get_logger

    setup_logging(session_id="abc123")
    logger = get_logger(__name__)  # → chips.agent.cli
    logger.info("hello")

模块日志文件（在 log/ 下）:
    chips.log         — 全量日志（汇总）
    memory.log        — chips.memory.*
    tool.log          — chips.tool.*
    session.log       — chips.session.*
    safety.log        — chips.safety.*
    config.log        — chips.config.*

依赖: safety.sanitize (redact)
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional

from safety.sanitize import redact

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "log")

# 模块名前缀 → 子日志文件映射
_MODULE_FILES: dict[str, str] = {
    "chips.memory": "memory.log",
    "chips.tool": "tool.log",
    "chips.session": "session.log",
    "chips.safety": "safety.log",
    "chips.config": "config.log",
}


# ── 格式化器 ──


class JSONFormatter(logging.Formatter):
    """JSON lines 格式化器 — 每行一个 JSON 对象。

    所有字段在序列化前经过 redact() 脱敏。
    """

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "name": record.name,
            "session": getattr(record, "session_id", ""),
            "msg": redact(record.getMessage()),
            "exc": redact(self.formatException(record.exc_info))
                   if record.exc_info else None,
        }, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """紧凑人类可读格式化器 — 控制台 stderr 输出。"""

    def format(self, record: logging.LogRecord) -> str:
        # 先通过 RedactingFormatter 风格的 format 做脱敏
        formatted = super().format(record)
        return redact(formatted)


# ── 过滤器 ──


class SessionFilter(logging.Filter):
    """向日志记录注入 session_id 属性。"""

    def __init__(self, session_id: str = ""):
        super().__init__()
        self.session_id = session_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = self.session_id or "-"
        return True


class PrefixFilter(logging.Filter):
    """只放行 logger 名完全匹配或带子级点的记录。

    例: PrefixFilter("chips.tool") 放行 chips.tool 和 chips.tool.registry，
    但不放行 chips.toolbox。
    """

    def __init__(self, prefix: str):
        super().__init__()
        self.prefix = prefix

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        return name == self.prefix or name.startswith(self.prefix + ".")


# ── 管理器 ──


class LogManager:
    """日志系统生命周期管理。单例。"""

    _instance: Optional["LogManager"] = None

    def __init__(self) -> None:
        self._chips = logging.getLogger("chips")
        self._session_filter = SessionFilter()
        self._managed: list[logging.Handler] = []

    @classmethod
    def instance(cls) -> "LogManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def setup(
        self,
        session_id: str = "",
        level: int = logging.INFO,
        log_dir: Optional[str] = None,
        console: bool = False,
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 3,
    ) -> logging.Logger:
        """配置日志系统。可多次调用，自动替换旧 handlers。"""
        self._teardown()

        log_dir = log_dir or _LOG_DIR
        os.makedirs(log_dir, exist_ok=True)

        # chips 必须设 level，否则 NOTSET → 继承 root(WARNING) → INFO 消息被拦截
        self._chips.setLevel(level)

        self._session_filter.session_id = session_id
        extra_filters = [self._session_filter]

        # 1. 主日志 chips.log — JSON 格式，全量
        main_h = self._build_handler(
            os.path.join(log_dir, "chips.log"),
            level, max_bytes, backup_count, JSONFormatter(), extra_filters,
        )
        self._add(main_h)

        # 2. 模块子日志 — 每个子文件一个 handler + 前缀过滤器
        for prefix, filename in _MODULE_FILES.items():
            h = self._build_handler(
                os.path.join(log_dir, filename),
                level, max_bytes, backup_count, JSONFormatter(),
                extra_filters + [PrefixFilter(prefix)],
            )
            self._add(h)

        # 3. 控制台 — 人类可读
        if console:
            console_h = self._build_handler(
                None,  # sys.stderr
                level, 0, 0, ConsoleFormatter("%(asctime)s [%(levelname)s] %(message)s  session=%(session_id)s"),
                extra_filters,
                stream=sys.stderr,
            )
            self._add(console_h)

        return self._chips

    def _build_handler(
        self,
        path: str | None,
        level: int,
        max_bytes: int,
        backup_count: int,
        fmt: logging.Formatter,
        filters: list[logging.Filter],
        stream=None,
    ) -> logging.Handler:
        if stream:
            h = logging.StreamHandler(stream)
        else:
            h = RotatingFileHandler(
                path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
            )
        h.setLevel(level)
        h.setFormatter(fmt)
        for f in filters:
            h.addFilter(f)
        return h

    def _add(self, handler: logging.Handler) -> None:
        self._chips.addHandler(handler)
        self._managed.append(handler)

    def _teardown(self) -> None:
        for h in self._managed:
            h.close()
            self._chips.removeHandler(h)
        self._managed.clear()

    def get_logger(self, name: str = "") -> logging.Logger:
        """获取 chips 层级下的 logger。无参数返回 chips 根 logger。"""
        return logging.getLogger(f"chips.{name}") if name else self._chips

    def set_session_id(self, session_id: str) -> None:
        self._session_filter.session_id = session_id


# ── 包级 API ──


def setup_logging(session_id: str = "", **kwargs) -> logging.Logger:
    """配置日志系统（兼容旧接口）。"""
    return LogManager.instance().setup(session_id=session_id, **kwargs)


def get_logger(name: str = "") -> logging.Logger:
    """获取 chips 层级下的 logger。

    无参数返回 chips 根 logger；传参返回 chips.<name>。
    第一方模块建议: logger = get_logger(__name__)  # → chips.agent.xxx
    """
    return LogManager.instance().get_logger(name)


def set_session_id(session_id: str) -> None:
    LogManager.instance().set_session_id(session_id)
