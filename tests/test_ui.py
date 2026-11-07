import os
import threading
import time
import unittest
from unittest import mock

from treefort import ui

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
RESET = "\033[0m"
CLEAR_LINE = "\r\033[K"


class StubStream:
    """Minimal text stream double with an explicit TTY state."""

    def __init__(self, isatty: bool = True):
        self._isatty = isatty
        self.chunks: list[str] = []

    def isatty(self) -> bool:
        return self._isatty

    def write(self, text: str) -> int:
        self.chunks.append(text)
        return len(text)

    def flush(self) -> None:
        pass

    @property
    def output(self) -> str:
        return "".join(self.chunks)


class UITestCase(unittest.TestCase):
    def tty_stream(self) -> StubStream:
        return StubStream(isatty=True)

    def plain_stream(self) -> StubStream:
        return StubStream(isatty=False)

    def no_color(self):
        return mock.patch.object(ui, "is_color_enabled", return_value=False)

    def color(self):
        return mock.patch.object(ui, "is_color_enabled", return_value=True)

    def test_is_color_enabled__tty_stream(self):
        self.assertTrue(ui.is_color_enabled(self.tty_stream()))

    def test_is_color_enabled__non_tty_stream(self):
        self.assertFalse(ui.is_color_enabled(self.plain_stream()))

    def test_is_color_enabled__stream_without_isatty(self):
        self.assertFalse(ui.is_color_enabled(object()))

    def test_is_color_enabled__no_color_env_set(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertFalse(ui.is_color_enabled(self.tty_stream()))

    def test_is_color_enabled__no_color_env_empty(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": ""}):
            self.assertTrue(ui.is_color_enabled(self.tty_stream()))

    def test_style_helpers__wrap_text_in_expected_sgr_codes(self):
        stream = self.tty_stream()
        expected_codes = {
            "bold": BOLD,
            "dim": DIM,
            "green": GREEN,
            "cyan": CYAN,
            "yellow": YELLOW,
            "red": RED,
            "blue": BLUE,
            "magenta": MAGENTA,
        }

        for helper_name, code in expected_codes.items():
            with self.subTest(helper=helper_name):
                helper = getattr(ui, helper_name)
                self.assertEqual(helper("text", stream), f"{code}text{RESET}")

    def test_style_helpers__omit_sgr_codes_without_color(self):
        stream = self.plain_stream()

        for helper_name in ("bold", "dim", "green", "cyan", "yellow", "red", "blue", "magenta"):
            with self.subTest(helper=helper_name):
                helper = getattr(ui, helper_name)
                self.assertEqual(helper("text", stream), "text")

    def test_success__renders_green_check_glyph(self):
        with self.color():
            self.assertEqual(ui.success("Done"), f"{GREEN}✓{RESET} Done")

    def test_success__omits_sgr_codes_without_color(self):
        with self.no_color():
            self.assertEqual(ui.success("Done"), "✓ Done")

    def test_step__renders_cyan_active_glyph(self):
        with self.color():
            self.assertEqual(ui.step("Running"), f"{CYAN}◎{RESET} Running")

    def test_step__omits_sgr_codes_without_color(self):
        with self.no_color():
            self.assertEqual(ui.step("Running"), "◎ Running")

    def test_gutter__renders_dim_bar_prefix(self):
        with self.color():
            self.assertEqual(ui.gutter("line"), f"  {DIM}│{RESET} line")

    def test_gutter__omits_sgr_codes_without_color(self):
        with self.no_color():
            self.assertEqual(ui.gutter("line"), "  │ line")

    def test_link__wraps_text_in_osc8_sequence(self):
        stream = self.tty_stream()
        expected = "\033]8;;https://github.com/octo/repo/pull/1\033\\PR #1\033]8;;\033\\"

        self.assertEqual(ui.link("PR #1", "https://github.com/octo/repo/pull/1", stream), expected)

    def test_link__returns_plain_text_without_color(self):
        stream = self.plain_stream()

        self.assertEqual(ui.link("PR #1", "https://github.com/octo/repo/pull/1", stream), "PR #1")

    def test_strip_ansi__removes_sgr_and_osc8_sequences(self):
        text = f"{BOLD}bold{RESET} \033]8;;https://example.test\033\\link\033]8;;\033\\"

        self.assertEqual(ui.strip_ansi(text), "bold link")

    def test_visible_len__ignores_sgr_sequences(self):
        self.assertEqual(ui.visible_len(f"{GREEN}✓{RESET} Done"), 6)

    def test_visible_len__ignores_osc8_sequences(self):
        linked = "\033]8;;https://example.test\033\\click\033]8;;\033\\"

        self.assertEqual(ui.visible_len(linked), 5)

    def test_visible_len__counts_plain_text(self):
        self.assertEqual(ui.visible_len("plain"), 5)

    def test_format_table__aligns_columns(self):
        with self.no_color():
            table = ui.format_table(
                ["NAME", "BRANCH"],
                [["feat", "main"], ["longer", "dev"]],
            )

        self.assertEqual(table, "NAME    BRANCH\nfeat    main\nlonger  dev")

    def test_format_table__dims_header_row(self):
        with self.color():
            table = ui.format_table(["NAME"], [["feat"]])

        self.assertEqual(table, f"{DIM}NAME{RESET}\nfeat")

    def test_format_table__aligns_cells_containing_sgr(self):
        status = f"{GREEN}ok{RESET}"

        with self.no_color():
            table = ui.format_table(["STATUS", "NAME"], [[status, "feature"], ["fail", "x"]])

        self.assertEqual(table, f"STATUS  NAME\n{status}      feature\nfail    x")

    def test_format_table__aligns_cells_containing_osc8(self):
        linked = ui.link("PR", "https://example.test", self.tty_stream())

        with self.no_color():
            table = ui.format_table(["REF", "NAME"], [[linked, "feature"], ["sha", "x"]])

        self.assertEqual(table, f"REF  NAME\n{linked}   feature\nsha  x")

    def test_format_table__truncates_columns_to_max_width(self):
        with self.no_color():
            table = ui.format_table(
                ["NAME", "DESC"],
                [["feat", "a very long description"]],
                max_width=20,
            )

        self.assertEqual(table, "NAME  DESC\nfeat  a very long d…")

    def test_format_table__uses_terminal_width_by_default(self):
        with self.no_color(), mock.patch.object(ui.shutil, "get_terminal_size") as size:
            size.return_value = os.terminal_size((20, 24))
            table = ui.format_table(
                ["NAME", "DESC"],
                [["feat", "a very long description"]],
            )

        self.assertEqual(table, "NAME  DESC\nfeat  a very long d…")

    def test_format_table__pads_short_rows(self):
        with self.no_color():
            table = ui.format_table(["NAME", "BRANCH"], [["feat"]])

        self.assertEqual(table, "NAME  BRANCH\nfeat")

    def test_format_tree__renders_root_and_sibling_connectors(self):
        with self.no_color():
            tree = ui.format_tree("main", [("develop", []), ("hotfix", [])])

        self.assertEqual(tree, "main\n├── develop\n└── hotfix")

    def test_format_tree__indents_nested_children(self):
        with self.no_color():
            tree = ui.format_tree(
                "main",
                [("develop", [("feature", []), ("fix", [])]), ("hotfix", [])],
            )

        self.assertEqual(
            tree,
            "main\n├── develop\n│   ├── feature\n│   └── fix\n└── hotfix",
        )

    def test_spinner__no_op_when_disabled(self):
        stream = self.tty_stream()

        with mock.patch.object(ui.sys, "stderr", stream):
            with ui.spinner("Fetching...", enabled=False):
                pass

        self.assertEqual(stream.output, "")

    def test_spinner__no_op_when_stderr_is_not_a_tty(self):
        stream = self.plain_stream()

        with mock.patch.object(ui.sys, "stderr", stream):
            with ui.spinner("Fetching...", enabled=True):
                pass

        self.assertEqual(stream.output, "")

    def test_spinner__renders_frames_and_clears_line(self):
        stream = self.tty_stream()

        with (
            mock.patch.object(ui.sys, "stderr", stream),
            mock.patch.object(ui, "is_color_enabled", return_value=True),
            mock.patch.object(ui, "SPINNER_INTERVAL", 0.001),
        ):
            with ui.spinner("Fetching..."):
                time.sleep(0.05)

        output = stream.output
        self.assertIn("Fetching...", output)
        self.assertTrue(any(frame in output for frame in ui.SPINNER_FRAMES))
        self.assertTrue(output.endswith(CLEAR_LINE))

    def test_spinner__stops_rendering_after_exit(self):
        stream = self.tty_stream()

        with (
            mock.patch.object(ui.sys, "stderr", stream),
            mock.patch.object(ui, "is_color_enabled", return_value=True),
            mock.patch.object(ui, "SPINNER_INTERVAL", 0.001),
        ):
            with ui.spinner("Fetching..."):
                time.sleep(0.02)

        length_after_exit = len(stream.output)
        time.sleep(0.02)

        self.assertEqual(len(stream.output), length_after_exit)

    def test_spinner__clears_line_when_body_raises(self):
        stream = self.tty_stream()

        with (
            mock.patch.object(ui.sys, "stderr", stream),
            mock.patch.object(ui, "is_color_enabled", return_value=True),
            mock.patch.object(ui, "SPINNER_INTERVAL", 0.001),
        ):
            with self.assertRaises(RuntimeError):
                with ui.spinner("Fetching..."):
                    raise RuntimeError("boom")

        self.assertTrue(stream.output.endswith(CLEAR_LINE))


class SpinnerThreadTestCase(unittest.TestCase):
    def test_spinner__terminates_background_thread(self):
        stream = StubStream(isatty=True)
        baseline = threading.active_count()

        with (
            mock.patch.object(ui.sys, "stderr", stream),
            mock.patch.object(ui, "is_color_enabled", return_value=True),
            mock.patch.object(ui, "SPINNER_INTERVAL", 0.001),
        ):
            with ui.spinner("Fetching..."):
                time.sleep(0.02)

        self.assertEqual(threading.active_count(), baseline)
