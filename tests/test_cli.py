import io
from contextlib import redirect_stderr, redirect_stdout
from unittest import TestCase
from unittest.mock import Mock, patch

from treefort.cli import main
from treefort.cli_manifest import ManifestUnavailable, load_manifest


class MainTestCase(TestCase):
    def run_main(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("treefort.cli.sys.argv", ["treefort", *argv]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            main()
        return stdout.getvalue(), stderr.getvalue()

    def run_main_expecting_exit(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("treefort.cli.sys.argv", ["treefort", *argv]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            self.assertRaises(SystemExit) as context,
        ):
            main()
        return context.exception.code, stdout.getvalue(), stderr.getvalue()

    def test_main__exits_with_a_clear_error_when_the_manifest_is_unavailable(self):
        with patch(
            "treefort.cli.load_manifest",
            side_effect=ManifestUnavailable("treefort is missing its generated CLI manifest"),
        ):
            code, stdout, _ = self.run_main_expecting_exit([])

        self.assertEqual("treefort: treefort is missing its generated CLI manifest", code)
        self.assertEqual("", stdout)

    def test_main__prints_root_help_without_building_the_component(self):
        with patch("treefort.cli.WorktreeCommands") as commands_mock:
            stdout, stderr = self.run_main([])

        commands_mock.assert_not_called()
        self.assertEqual(load_manifest().root_help_text, stdout)
        self.assertEqual("", stderr)

    def test_main__prints_command_help_for_the_help_flag(self):
        with patch("treefort.cli.WorktreeCommands") as commands_mock:
            stdout, stderr = self.run_main(["create", "--help"])

        commands_mock.assert_not_called()
        self.assertEqual(load_manifest().commands["create"].help_text, stdout)
        self.assertEqual("", stderr)

    def test_main__prints_command_help_for_an_alias(self):
        with patch("treefort.cli.WorktreeCommands") as commands_mock:
            stdout, _ = self.run_main(["rm", "-h"])

        commands_mock.assert_not_called()
        self.assertEqual(load_manifest().commands["remove"].help_text, stdout)

    def test_main__prints_command_help_for_the_help_subcommand(self):
        with patch("treefort.cli.WorktreeCommands") as commands_mock:
            stdout, _ = self.run_main(["help", "install"])

        commands_mock.assert_not_called()
        self.assertEqual(load_manifest().commands["install"].help_text, stdout)

    def test_main__dispatches_the_parsed_invocation(self):
        component = Mock()
        with patch("treefort.cli.WorktreeCommands", return_value=component):
            self.run_main(["rm", "feature", "--force"])

        component.remove.assert_called_once_with("feature", force=True)

    def test_main__passes_verbosity_to_the_component(self):
        component = Mock()
        with patch("treefort.cli.WorktreeCommands", return_value=component) as commands_mock:
            self.run_main(["-v", "version"])

        commands_mock.assert_called_once_with(verbose=True)
        component.version.assert_called_once_with()

    def test_main__exits_with_2_and_reports_the_root_synopsis(self):
        code, stdout, stderr = self.run_main_expecting_exit(["bogus"])

        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertEqual(
            "✗ Unknown command 'bogus'.\n"
            "treefort <command> [args...] [flags...]\n"
            "Run 'treefort --help' for usage.\n",
            stderr,
        )

    def test_main__exits_with_2_and_reports_the_command_synopsis(self):
        code, _, stderr = self.run_main_expecting_exit(["create"])

        self.assertEqual(2, code)
        self.assertEqual(
            "✗ Missing required argument: WORKTREE_NAME\n"
            "treefort create WORKTREE_NAME [BASE_REF] <flags>\n"
            "Run 'treefort create --help' for usage.\n",
            stderr,
        )

    def test_main__exits_with_2_for_an_alias_under_the_canonical_name(self):
        code, _, stderr = self.run_main_expecting_exit(["rm"])

        self.assertEqual(2, code)
        self.assertIn("Run 'treefort remove --help' for usage.", stderr)

    def test_main__logs_and_exits_with_1_when_the_command_fails(self):
        component = Mock()
        component._runtime.verbose = False
        component.remove.side_effect = RuntimeError("boom")
        with patch("treefort.cli.WorktreeCommands", return_value=component):
            code, _, _ = self.run_main_expecting_exit(["rm", "feature"])

        self.assertEqual(1, code)
        component._logger.error.assert_called_once_with("Command failed: boom")

    def test_main__reraises_when_the_command_was_verbose(self):
        component = Mock()
        component._runtime.verbose = False
        component.remove.side_effect = RuntimeError("boom")
        with patch("treefort.cli.WorktreeCommands", return_value=component):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                self.run_main(["-v", "rm", "feature"])

        component._logger.error.assert_not_called()

    def test_main__reraises_when_the_runtime_is_verbose(self):
        component = Mock()
        component._runtime.verbose = True
        component.remove.side_effect = RuntimeError("boom")
        with patch("treefort.cli.WorktreeCommands", return_value=component):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                self.run_main(["rm", "feature"])

        component._logger.error.assert_not_called()

    def test_main__exits_with_1_when_the_component_cannot_be_built(self):
        with (
            patch(
                "treefort.cli.WorktreeCommands",
                _name="treefort",
                side_effect=RuntimeError("invalid config"),
            ),
            patch("treefort.cli.logging.getLogger") as get_logger_mock,
        ):
            code, _, _ = self.run_main_expecting_exit(["version"])

        self.assertEqual(1, code)
        get_logger_mock.assert_called_once_with("treefort")
        get_logger_mock.return_value.error.assert_called_once_with("Command failed: invalid config")
