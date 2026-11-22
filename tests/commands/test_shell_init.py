import inspect
import io
import os
import shutil
import subprocess
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import get_args
from unittest import TestCase
from unittest.mock import patch

from treefort.cli_manifest import load_manifest
from treefort.commands.shell_init import ShellInitCommand
from treefort.errors import ValidationError
from treefort.shells import CMD_PLACEHOLDER, SHELLS, wrapper


class ShellInitCommandTestCase(TestCase):
    def setUp(self):
        self.command = ShellInitCommand(None)

    def emit(self, *args, argv0="treefort") -> str:
        """Invoke the command with a deterministic ``sys.argv[0]``."""
        stdout = io.StringIO()
        with patch("sys.argv", [argv0]), redirect_stdout(stdout):
            self.command(*args)
        return stdout.getvalue()

    def test_call__declares_the_cli_contract(self):
        parameters = inspect.signature(ShellInitCommand.__call__).parameters
        literal, none_type = get_args(parameters["shell"].annotation)

        self.assertEqual("shell-init", ShellInitCommand._name)
        self.assertEqual((), tuple(ShellInitCommand._aliases))
        self.assertIs(False, ShellInitCommand._requires_runtime)
        self.assertEqual(["self", "shell"], list(parameters))
        self.assertIs(inspect.Parameter.POSITIONAL_OR_KEYWORD, parameters["shell"].kind)
        self.assertIsNone(parameters["shell"].default)
        self.assertEqual(("bash", "zsh", "fish"), get_args(literal))
        self.assertIs(type(None), none_type)

    def test_call__emits_the_wrapper_and_the_manifest_completions(self):
        manifest = load_manifest()

        for shell in SHELLS:
            with self.subTest(shell=shell):
                expected_completions = manifest.shell_completions[shell].replace(
                    CMD_PLACEHOLDER, "treefort"
                )

                self.assertEqual(
                    f"{wrapper(shell, 'treefort')}\n\n{expected_completions}", self.emit(shell)
                )

    def test_call__emits_the_switch_alias_and_worktree_candidates(self):
        script = self.emit("bash")

        self.assertIn("TREEFORT_CD_FILE", script)
        self.assertIn("switch|sw)", script)
        self.assertIn("remove|rm)", script)
        self.assertIn("complete -F _treefort_complete treefort", script)
        self.assertIn("_treefort_worktrees", script)

    def test_call__infers_the_command_name_from_argv0(self):
        output = self.emit("bash", argv0="/usr/local/bin/tf")

        self.assertIn("tf() {", output)
        self.assertIn("complete -F _treefort_complete tf", output)

    def test_call__auto_detects_the_shell_from_the_environment(self):
        with patch.dict(os.environ, {"SHELL": "/usr/local/bin/zsh"}):
            output = self.emit()

        self.assertIn("bashcompinit", output)

    def test_call__raises_when_the_shell_cannot_be_detected(self):
        with patch.dict(os.environ, {"SHELL": "/bin/pwsh"}):
            with self.assertRaisesRegex(
                ValidationError, "Could not detect a supported shell from \\$SHELL"
            ):
                self.command()

    def test_call__raises_for_an_unsupported_shell(self):
        with self.assertRaisesRegex(ValidationError, "Unsupported shell: 'pwsh'"):
            self.command("pwsh")

    def test_call__emits_bash_that_parses(self):
        if not shutil.which("bash"):
            self.skipTest("bash is not installed")

        result = subprocess.run(
            ["bash", "-n"],
            input=self.emit("bash"),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def test_call__holds_no_runtime_reference(self):
        command = ShellInitCommand(None)

        self.assertFalse(hasattr(command, "_runtime"))

    def test_call__does_not_create_the_treefort_directory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME": tmp}):
            self.emit("bash")

            self.assertEqual([], list(Path(tmp).iterdir()))
