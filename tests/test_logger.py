import io
import logging
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from treefort.logger import COLORS, ColorFormatter, PlainFileFormatter, setup_logger


class _TtyStringIO(io.StringIO):
    def __init__(self, is_tty: bool):
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


class ColorFormatterTestCase(TestCase):
    def test_does_not_prefix_message_for_base_logger_namespace(self):
        formatter = ColorFormatter(
            "%(message)s",
            use_color=False,
            base_logger_name="treefort",
        )
        record = logging.LogRecord(
            name="treefort.gh",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )

        self.assertEqual(formatter.format(record), "hello")

    def test_prefixes_message_for_external_logger(self):
        formatter = ColorFormatter(
            "%(message)s",
            use_color=False,
            base_logger_name="treefort",
        )
        record = logging.LogRecord(
            name="urllib3.connectionpool",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )

        self.assertEqual(formatter.format(record), "urllib3.connectionpool | hello")

    def test_format__prefixes_warning_and_error_with_glyphs(self):
        plain_formatter = ColorFormatter(
            "%(message)s", use_color=False, base_logger_name="treefort"
        )
        color_formatter = ColorFormatter("%(message)s", use_color=True, base_logger_name="treefort")

        warn_record = logging.LogRecord(
            name="treefort",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="watch out",
            args=(),
            exc_info=None,
        )
        err_record = logging.LogRecord(
            name="treefort",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=None,
        )

        self.assertEqual(plain_formatter.format(warn_record), "▲ watch out")
        self.assertEqual(plain_formatter.format(err_record), "✗ failed")
        self.assertEqual(
            color_formatter.format(warn_record),
            f"{COLORS['yellow']}▲ watch out{COLORS['reset']}",
        )
        self.assertEqual(
            color_formatter.format(err_record),
            f"{COLORS['red']}✗ failed{COLORS['reset']}",
        )


class PlainFileFormatterTestCase(TestCase):
    def test_format__strips_ansi_sgr_and_osc8_sequences(self):
        formatter = PlainFileFormatter("%(levelname)s | %(message)s")
        msg = (
            "\033[2K"
            f"{COLORS['green']}✓{COLORS['reset']} "
            "\033]8;id=1;https://github.com/octo/repo/pull/1\033\\PR #1\033]8;;\033\\ "
            "\033[1mbold\033[0m"
        )
        record = logging.LogRecord(
            name="treefort",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )

        self.assertEqual(formatter.format(record), "INFO | ✓ PR #1 bold")


class SetupLoggerTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.tmp_dir.name) / "logs"
        root = logging.getLogger()
        self._orig_handlers = list(root.handlers)
        self._orig_level = root.level

    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            if handler not in self._orig_handlers:
                root.removeHandler(handler)
                handler.close()
        for handler in self._orig_handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(self._orig_level)
        self.tmp_dir.cleanup()

    def test_setup_logger__records_debug_to_file_and_strips_ansi_when_not_verbose(self):
        stdout = _TtyStringIO(is_tty=False)
        stderr = _TtyStringIO(is_tty=False)

        with (
            patch("treefort.logger.sys.stdout", stdout),
            patch("treefort.logger.sys.stderr", stderr),
        ):
            logger = setup_logger("treefort", log_dir=self.log_dir, verbose=False)
            logger.debug("\033[94mSTART: git status\033[0m")
            logger.info("\033[92m✓\033[0m \033]8;;https://example.com\033\\done\033]8;;\033\\")

        self.assertEqual(stdout.getvalue().strip(), "✓ done")
        self.assertEqual(stderr.getvalue(), "")

        log_contents = (self.log_dir / "treefort.log").read_text(encoding="utf-8")
        self.assertIn("[DEBUG] treefort | START: git status", log_contents)
        self.assertIn("[INFO] treefort | ✓ done", log_contents)
        self.assertNotIn("\033", log_contents)

    def test_setup_logger__routes_warnings_and_errors_to_stderr(self):
        stdout = _TtyStringIO(is_tty=False)
        stderr = _TtyStringIO(is_tty=False)

        with (
            patch("treefort.logger.sys.stdout", stdout),
            patch("treefort.logger.sys.stderr", stderr),
        ):
            logger = setup_logger("treefort", log_dir=self.log_dir, verbose=False)
            logger.info("normal output")
            logger.warning("disk space low")
            logger.error("command failed")

        self.assertEqual(stdout.getvalue().splitlines(), ["normal output"])
        self.assertEqual(
            stderr.getvalue().splitlines(),
            ["▲ disk space low", "✗ command failed"],
        )

    def test_setup_logger__respects_no_color_and_tty(self):
        stdout = _TtyStringIO(is_tty=True)
        stderr = _TtyStringIO(is_tty=True)

        with (
            patch.dict(os.environ, {"NO_COLOR": "1"}),
            patch("treefort.logger.sys.stdout", stdout),
            patch("treefort.logger.sys.stderr", stderr),
        ):
            logger = setup_logger("treefort", log_dir=self.log_dir, verbose=True)
            logger.debug("debug trace")
            logger.warning("careful")
            logger.error("boom")

        self.assertEqual(stdout.getvalue().splitlines(), ["debug trace"])
        self.assertEqual(stderr.getvalue().splitlines(), ["▲ careful", "✗ boom"])
        self.assertNotIn("\033", stdout.getvalue())
        self.assertNotIn("\033", stderr.getvalue())
