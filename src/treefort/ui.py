"""Zero-dependency terminal presentation helpers for treefort.

This module is a pure-standard-library leaf. It must never import from ``treefort``, so it can be
used by any layer without creating a dependency cycle.
"""

import os
import re
import shutil
import sys
import threading
from contextlib import contextmanager
from typing import TextIO

ANSI_RE = re.compile(r"\033\]8;[^;\033\007]*;.*?(?:\033\\|\007)|\033\[[0-9;]*[a-zA-Z]")

RESET = "\033[0m"
CLEAR_LINE = "\r\033[K"
OSC8_CLOSE = "\033]8;;\033\\"

STYLES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
}

# The color subset of STYLES, for callers that choose a foreground color by name
COLORS = {name: code for name, code in STYLES.items() if name not in ("bold", "dim")}

SUCCESS_GLYPH = "✓"
STEP_GLYPH = "◎"
GUTTER_GLYPH = "│"

TREE_MIDDLE = "├── "
TREE_LAST = "└── "
TREE_VERTICAL = "│   "
TREE_BLANK = "    "

COLUMN_SEPARATOR = "  "
MIN_COLUMN_WIDTH = 2
ELLIPSIS = "…"

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_INTERVAL = 0.08
SPINNER_JOIN_TIMEOUT = 1.0


def strip_ansi(text: str) -> str:
    """Return ``text`` without SGR or OSC 8 escape sequences."""
    return ANSI_RE.sub("", text)


def is_color_enabled(stream: TextIO | None = None) -> bool:
    """Return whether styled output should be emitted for ``stream``.

    Styling is disabled when ``NO_COLOR`` is set or the stream is not a TTY.
    """
    if os.environ.get("NO_COLOR"):
        return False

    stream = sys.stdout if stream is None else stream
    isatty = getattr(stream, "isatty", None)
    if isatty is None:
        return False

    try:
        return bool(isatty())
    except (ValueError, OSError):
        return False


def _style(code: str, text: str, stream: TextIO | None) -> str:
    if not is_color_enabled(stream):
        return text
    return f"{code}{text}{RESET}"


def bold(text: str, stream: TextIO | None = None) -> str:
    return _style(STYLES["bold"], text, stream)


def dim(text: str, stream: TextIO | None = None) -> str:
    return _style(STYLES["dim"], text, stream)


def red(text: str, stream: TextIO | None = None) -> str:
    return _style(STYLES["red"], text, stream)


def green(text: str, stream: TextIO | None = None) -> str:
    return _style(STYLES["green"], text, stream)


def yellow(text: str, stream: TextIO | None = None) -> str:
    return _style(STYLES["yellow"], text, stream)


def blue(text: str, stream: TextIO | None = None) -> str:
    return _style(STYLES["blue"], text, stream)


def magenta(text: str, stream: TextIO | None = None) -> str:
    return _style(STYLES["magenta"], text, stream)


def cyan(text: str, stream: TextIO | None = None) -> str:
    return _style(STYLES["cyan"], text, stream)


def success(message: str) -> str:
    """Format a completed milestone line."""
    return f"{green(SUCCESS_GLYPH)} {message}"


def step(message: str) -> str:
    """Format an in-progress step line, such as a running hook."""
    return f"{cyan(STEP_GLYPH)} {message}"


def gutter(line: str) -> str:
    """Format a streamed output line beneath a step line."""
    return f"  {dim(GUTTER_GLYPH)} {line}"


def link(text: str, url: str, stream: TextIO | None = None) -> str:
    """Wrap ``text`` in an OSC 8 hyperlink when the terminal supports styling."""
    if not is_color_enabled(stream):
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def visible_len(text: str) -> int:
    """Return the printable length of ``text``, ignoring escape sequences.

    Length is counted in code points, not display columns, so wide characters such as CJK text or
    emoji are not accounted for.
    """
    return len(strip_ansi(text))


def _column_widths(headers: list[str], rows: list[list[str]]) -> list[int]:
    column_count = max([len(headers), *(len(row) for row in rows)])
    widths = [0] * column_count

    for row in [headers, *rows]:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], visible_len(cell))

    return widths


def _fit_widths(widths: list[int], max_width: int) -> list[int]:
    """Shrink columns right to left until the row fits within ``max_width``."""
    fitted = list(widths)
    if not fitted:
        return fitted

    total = sum(fitted) + len(COLUMN_SEPARATOR) * (len(fitted) - 1)
    for index in range(len(fitted) - 1, -1, -1):
        if total <= max_width:
            break

        reducible = fitted[index] - MIN_COLUMN_WIDTH
        if reducible <= 0:
            continue

        shrink = min(reducible, total - max_width)
        fitted[index] -= shrink
        total -= shrink

    return fitted


def _truncate(text: str, width: int) -> str:
    """Truncate ``text`` to ``width`` printable characters, preserving escape sequences."""
    if visible_len(text) <= width:
        return text

    limit = max(width - 1, 0)
    parts = []
    visible = 0
    position = 0

    while position < len(text) and visible < limit:
        match = ANSI_RE.match(text, position)
        if match:
            parts.append(match.group())
            position = match.end()
            continue
        parts.append(text[position])
        visible += 1
        position += 1

    truncated = "".join(parts)
    if "\033[" in truncated:
        truncated += RESET
    if "\033]8;" in truncated:
        # A link cut mid-sequence would otherwise keep the hyperlink open across later columns
        truncated += OSC8_CLOSE
    return f"{truncated}{ELLIPSIS}"


def _fit_cell(text: str, width: int) -> str:
    cell = _truncate(text, width)
    return cell + " " * max(width - visible_len(cell), 0)


def format_table(
    headers: list[str],
    rows: list[list[str]],
    max_width: int | None = None,
) -> str:
    """Render ``rows`` as an aligned table with a dimmed header row.

    Column padding is measured on printable width, so cells may contain SGR styling or OSC 8
    links. Columns are truncated from the right with ``…`` when the table exceeds ``max_width``,
    which defaults to the terminal width. No column is narrowed below ``MIN_COLUMN_WIDTH``, so a
    table with too many columns can still exceed ``max_width``.
    """
    widths = _column_widths(headers, rows)
    if not widths:
        return ""

    if max_width is None:
        max_width = shutil.get_terminal_size().columns
    widths = _fit_widths(widths, max_width)

    def render(row: list[str]) -> str:
        cells = [row[index] if index < len(row) else "" for index in range(len(widths))]
        line = COLUMN_SEPARATOR.join(
            _fit_cell(cell, width) for cell, width in zip(cells, widths, strict=False)
        )
        return line.rstrip()

    lines = [dim(render(headers))] if headers else []
    lines.extend(render(row) for row in rows)
    return "\n".join(lines)


def _append_tree_lines(lines: list[str], children: list[tuple[str, list]], prefix: str) -> None:
    last_index = len(children) - 1

    for index, (label, grandchildren) in enumerate(children):
        is_last = index == last_index
        connector = TREE_LAST if is_last else TREE_MIDDLE
        lines.append(f"{prefix}{dim(connector)}{label}")
        extension = TREE_BLANK if is_last else TREE_VERTICAL
        _append_tree_lines(lines, grandchildren or [], prefix + extension)


def format_tree(root_label: str, children: list[tuple[str, list]]) -> str:
    """Render ``root_label`` and nested ``children`` with Unicode box-drawing connectors."""
    lines = [root_label]
    _append_tree_lines(lines, children, prefix="")
    return "\n".join(lines)


def _emit(stream: TextIO, text: str) -> bool:
    """Write ``text`` to ``stream``, returning whether the write succeeded.

    Terminal streams can disappear mid-render, such as when the reading end closes. Spinner output
    is cosmetic, so a failed write must never displace the error that ended the surrounding work.
    """
    try:
        stream.write(text)
        stream.flush()
        return True
    except (OSError, ValueError):
        return False


@contextmanager
def spinner(message: str, enabled: bool = True):
    """Render a transient braille spinner on ``stderr`` while the body runs.

    The spinner is a no-op when disabled, when ``NO_COLOR`` is set, or when ``stderr`` is not a
    TTY. The rendered line is always cleared on exit, including when the body raises.

    Frames are written without a trailing newline and cleared with each redraw, so log records
    emitted inside the body are erased by the next frame. Only wrap work whose logging stays at
    ``DEBUG``; user-facing output belongs outside the spinner.
    """
    stream = sys.stderr
    if not (enabled and is_color_enabled(stream)):
        yield
        return

    stop = threading.Event()

    def render() -> None:
        frame_index = 0
        while not stop.is_set():
            frame = SPINNER_FRAMES[frame_index % len(SPINNER_FRAMES)]
            if not _emit(stream, f"{CLEAR_LINE}{cyan(frame, stream)} {message}"):
                break
            frame_index += 1
            stop.wait(SPINNER_INTERVAL)

    thread = threading.Thread(target=render, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=SPINNER_JOIN_TIMEOUT)
        _emit(stream, CLEAR_LINE)
