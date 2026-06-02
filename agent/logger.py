"""日志系统 — 旋转日志 + Session 标记 + 脱敏"""

import logging
import os
from logging.handlers import RotatingFileHandler

from safety.sanitize import RedactingFormatter

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "log")
_LOG_FILE = os.path.join(_LOG_DIR, "chips.log")


class SessionFilter(logging.Filter):
    """向日志记录注入 session_id 字段。"""

    def __init__(self, session_id: str = ""):
        super().__init__()
        self.session_id = session_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = self.session_id or "-"
        return True


def setup_logging(session_id: str = "", level: int = logging.INFO,
                  max_bytes: int = 5 * 1024 * 1024, backup_count: int = 3) -> logging.Logger:
    """配置应用日志。

    Args:
        session_id: 当前会话 ID，用于日志标记。
        level: 日志级别。
        max_bytes: 单个日志文件最大字节数。
        backup_count: 保留的历史日志文件数。

    Returns:
        配置好的根 logger。
    """
    os.makedirs(_LOG_DIR, exist_ok=True)

    logger = logging.getLogger("chips")
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(RedactingFormatter(
        "%(asctime)s [%(levelname)s] session=%(session_id)s %(message)s",
    ))
    handler.addFilter(SessionFilter(session_id))
    logger.addHandler(handler)

    return logger


def get_logger() -> logging.Logger:
    """获取 chips logger。"""
    return logging.getLogger("chips")
