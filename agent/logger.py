"""日志系统 — 多文件旋转日志 + Session 标记 + 脱敏

日志文件结构（log/ 下）：
  chips.log       — 主日志（所有应用模块的汇总）
  memory.log      — memory.* 模块
  tool.log        — tool.* 模块
  session.log     — session.* 模块
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from safety.sanitize import RedactingFormatter

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "log")
_LOG_FILE = os.path.join(_LOG_DIR, "chips.log")

# 子日志文件映射：logger 名前缀 → 文件名
_SUB_LOG_FILES: dict[str, str] = {
    "memory": "memory.log",
    "tool": "tool.log",
    "session": "session.log",
    "safety": "safety.log",
    "config": "config.log",
}


class SessionFilter(logging.Filter):
    """向日志记录注入 session_id 字段。"""

    def __init__(self, session_id: str = ""):
        super().__init__()
        self.session_id = session_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = self.session_id or "-"
        return True


def _make_handler(path: str, level: int, max_bytes: int,
                  backup_count: int, session_id: str,
                  fmt: str) -> RotatingFileHandler:
    h = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
    )
    h.setLevel(level)
    h.setFormatter(RedactingFormatter(fmt))
    h.addFilter(SessionFilter(session_id))
    return h


def setup_logging(session_id: str = "", level: int = logging.INFO,
                  max_bytes: int = 5 * 1024 * 1024, backup_count: int = 3) -> logging.Logger:
    """配置多文件应用日志。

    为每个子模块创建独立的日志文件，同时汇总到 chips.log。

    Args:
        session_id: 当前会话 ID，用于日志标记。
        level: 日志级别。
        max_bytes: 单个日志文件最大字节数。
        backup_count: 保留的历史日志文件数。

    Returns:
        配置好的 chips logger。
    """
    os.makedirs(_LOG_DIR, exist_ok=True)

    logger = logging.getLogger("chips")
    logger.setLevel(level)

    if logger.handlers:
        return logger

    base_fmt = "%(asctime)s [%(levelname)s] session=%(session_id)s %(message)s"
    name_fmt = "%(asctime)s [%(levelname)s] %(name)s session=%(session_id)s %(message)s"

    # ── chips.log（主日志，汇总） ──
    chips_handler = _make_handler(_LOG_FILE, level, max_bytes, backup_count, session_id, base_fmt)
    logger.addHandler(chips_handler)

    # ── 子模块日志文件 ──
    sub_handlers: dict[str, logging.Handler] = {}
    for module, filename in _SUB_LOG_FILES.items():
        path = os.path.join(_LOG_DIR, filename)
        h = _make_handler(path, level, max_bytes, backup_count, session_id, name_fmt)
        h.name = f"sub_{module}"
        sub_handlers[module] = h

    # ── root logger — 收集 __name__ 日志，分流到子文件 ──
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(level)
        for module, h in sub_handlers.items():
            # 只收对应前缀的日志
            h.addFilter(logging.Filter(name=module))
            root.addHandler(h)

    return logger


def get_logger() -> logging.Logger:
    """获取 chips logger。"""
    return logging.getLogger("chips")
