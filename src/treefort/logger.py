"""Logging utility for treefort CLI."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from treefort import ui

# ANSI color codes, sourced from the terminal UI palette so there is a single definition
COLORS = {**ui.COLORS, "reset": ui.RESET}
MAX_LOG_SIZE = 10 * 1024 * 1024
LOG_BACKUP = 5


class PlainFileFormatter(logging.Formatter):
    """Formatter that strips ANSI SGR and OSC 8 escape sequences for file logs."""

    def format(self, record: logging.LogRecord) -> str:
        return ui.strip_ansi(super().format(record))


class ColorFormatter(logging.Formatter):
    """Formatter that applies colors based on log level or explicit color."""

    LEVEL_COLORS = {
        logging.DEBUG: COLORS["blue"],
        logging.WARNING: COLORS["yellow"],
        logging.ERROR: COLORS["red"],
        logging.CRITICAL: COLORS["red"],
    }
    LEVEL_PREFIXES = {
        logging.WARNING: "▲ ",
        logging.ERROR: "✗ ",
        logging.CRITICAL: "✗ ",
    }

    def __init__(self, fmt: str, use_color: bool = True, base_logger_name: str | None = None):
        super().__init__(fmt)
        self.use_color = use_color
        self.base_logger_name = base_logger_name
        self.user_dir = Path("~").expanduser()

    def format(self, record: logging.LogRecord) -> str:
        # Use explicit color from extra if provided, otherwise use level-based color
        color = getattr(record, "color", None)
        if color and color in COLORS:
            color_code = COLORS[color]
        else:
            color_code = self.LEVEL_COLORS.get(record.levelno, COLORS["reset"])

        message = super().format(record).replace(f"{self.user_dir}/", "~/")
        if self.base_logger_name and not record.name.startswith(self.base_logger_name):
            message = f"{record.name} | {message}"
        prefix = self.LEVEL_PREFIXES.get(record.levelno, "")
        message = f"{prefix}{message}"
        if not self.use_color:
            return ui.strip_ansi(message)
        if color_code:
            return f"{color_code}{message}{COLORS['reset']}"
        return message


def setup_logger(
    name: str,
    log_dir: Path | None = None,
    verbose: bool = False,
    max_log_size: int = MAX_LOG_SIZE,
) -> logging.Logger:
    """
    Set up logging with console and file handlers.

    :param name: Logger name
    :param log_dir: Directory for log files (defaults to ~/.treefort/logs)
    :param verbose: Enable debug logging with full subprocess output
    :param max_log_size: Maximum size of log files in bytes
    :return: Configured logger
    """
    console_level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = []

    # Console formatters - colored, no timestamp for user-facing output
    console_fmt = "%(message)s"
    stdout_formatter = ColorFormatter(
        console_fmt,
        use_color=ui.is_color_enabled(sys.stdout),
        base_logger_name=name,
    )
    stderr_formatter = ColorFormatter(
        console_fmt,
        use_color=ui.is_color_enabled(sys.stderr),
        base_logger_name=name,
    )

    # Stdout console handler (DEBUG/INFO only)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.addFilter(lambda record: record.levelno < logging.WARNING)
    console_handler.setFormatter(stdout_formatter)
    handlers.append(console_handler)

    # Stderr console handler (WARNING/ERROR/CRITICAL)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(stderr_formatter)
    handlers.append(stderr_handler)

    # File handlers (if log_dir provided)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

        # Default log file - timestamps and commands
        default_log_file = log_dir / "treefort.log"
        file_fmt = "%(asctime)s [%(levelname)s] %(name)s | %(message)s"
        file_formatter = PlainFileFormatter(file_fmt, datefmt="%Y-%m-%d %H:%M:%S")

        file_handler = RotatingFileHandler(
            default_log_file, maxBytes=max_log_size, backupCount=LOG_BACKUP, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)
        handlers.append(file_handler)

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=handlers,
        force=True,
    )

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    return logger
