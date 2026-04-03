"""
log — centralized loguru configuration.

Call setup_logging() once at process startup (tui.py / server.py).
All other modules just: from loguru import logger
"""

from pathlib import Path

from loguru import logger

from .config import LOG_DIR, LOG_LEVEL


def setup_logging(log_dir: Path = LOG_DIR, level: str = LOG_LEVEL, stderr: bool = False) -> None:
    """
    Remove the default stderr sink and add a rotating file sink.
    Safe to call multiple times — removes all existing sinks first.

    stderr=True 时额外向终端输出日志（server.py 使用）。
    """
    logger.remove()  # drop default stderr output

    log_dir.mkdir(parents=True, exist_ok=True)

    log_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "{level:<8} | "
        "{name}:{function}:{line} | "
        "{message}"
    )

    logger.add(
        log_dir / "ccserver.log",
        level=level,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        enqueue=True,   # async-safe, single writer thread
        format=log_format,
    )

    if stderr:
        import sys
        try:
            tty = open("/dev/tty", "w")
            logger.add(tty, level=level, format=log_format, colorize=True)
        except OSError:
            # 没有 tty（如 Docker/CI 环境），降级到 stderr 无颜色
            logger.add(sys.stderr, level=level, format=log_format, colorize=False)
