"""Offline integration coverage for worktree switching and shell integration.

Every test runs against the local-Git fixture in :mod:`tests.integration.base`, so no test
reaches the network, the developer's ``HOME``, or the developer's Git configuration.
"""

import io
import os
import shlex
import shutil
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from treefort.commands.prune import PruneCommand
from treefort.commands.remove import RemoveCommand
from treefort.commands.shell_init import ShellInitCommand
from treefort.commands.switch import SwitchCommand
from treefort.context import CD_FILE_ENV_VAR
from treefort.errors import WorktreeNotFoundError

from .base import LocalRepoIntegrationTestCase


class SwitchIntegrationTestCase(LocalRepoIntegrationTestCase):
    """Exercises ``treefort switch`` against real ``git worktree`` state."""

    def setUp(self):
        super().setUp()
        self.runtime.git.fetch()
        self.runtime.git.add_worktree("subpath/feature", f"origin/{self.default_branch}")
        self.runtime.git.add_worktree("125", f"origin/{self.default_branch}")

    def enter(self, path: Path) -> None:
        """Change into ``path`` and keep the context's recorded directory in step."""
        os.chdir(path)
        self.runtime.context.cwd = Path.cwd()

    def run_command(self, command, *args, **kwargs):
        """Run a command with captured streams."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            command(*args, **kwargs)
        return stdout.getvalue(), stderr.getvalue()

    def directive_file(self) -> Path:
        """Return a fresh directive file the wrapper would have created."""
        path = self.temp_path / "cd-directive"
        path.write_text("", encoding="utf-8")
        return path

    def test_switch__switches_across_managed_worktrees(self):
        directive = self.directive_file()

        for name in ("subpath/feature", "125"):
            with self.subTest(name=name):
                with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(directive)}):
                    stdout, stderr = self.run_command(SwitchCommand(self.runtime), name)

                self.assertEqual("", stdout)
                self.assertEqual("", stderr)
                self.assertEqual(
                    f"{self.project_dir / name}\n", directive.read_text(encoding="utf-8")
                )

    def test_switch__records_and_toggles_the_previous_worktree(self):
        directive = self.directive_file()
        self.enter(self.project_dir / "subpath" / "feature")

        with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(directive)}):
            self.run_command(SwitchCommand(self.runtime), "125")
            self.assertEqual("subpath/feature", self.runtime.context.get_config().last_worktree)

            # The wrapper performs the cd, so the next invocation starts in the new worktree
            self.enter(self.project_dir / "125")
            self.run_command(SwitchCommand(self.runtime), "-")

        self.assertEqual(f"{self.project_dir / 'subpath' / 'feature'}\n", directive.read_text())
        self.assertEqual("125", self.runtime.context.get_config().last_worktree)

    def test_switch__rejects_a_stale_previous_worktree(self):
        directive = self.directive_file()
        self.enter(self.project_dir / "125")

        with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(directive)}):
            self.run_command(SwitchCommand(self.runtime), "subpath/feature")
            self.assertEqual("125", self.runtime.context.get_config().last_worktree)
            # Leave the directory before removing it, so the process cwd stays valid
            self.enter(self.project_dir)
            self.runtime.git.remove_worktree("125")

            with self.assertRaisesRegex(
                WorktreeNotFoundError, "Previous worktree '125' is not a worktree"
            ):
                self.run_command(SwitchCommand(self.runtime), "-")

    def test_switch__prints_the_path_when_the_directive_is_unset(self):
        self.enter(self.project_dir)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(CD_FILE_ENV_VAR, None)
            stdout, stderr = self.run_command(SwitchCommand(self.runtime), "125")

        self.assertEqual(f"{self.project_dir / '125'}\n", stdout)
        self.assertIn("treefort install --shell", stderr)

    def test_switch__treats_a_subdirectory_as_the_enclosing_worktree(self):
        directive = self.directive_file()
        nested = self.project_dir / "subpath" / "feature" / "src"
        nested.mkdir()
        self.enter(nested)

        with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(directive)}):
            self.run_command(SwitchCommand(self.runtime), "@")

        self.assertEqual(f"{self.project_dir / 'subpath' / 'feature'}\n", directive.read_text())


class DeletedWorktreeRecoveryIntegrationTestCase(LocalRepoIntegrationTestCase):
    """Exercises recovery when ``remove`` or ``prune`` deletes the starting directory."""

    def setUp(self):
        super().setUp()
        self.runtime.git.fetch()
        self.runtime.git.add_worktree("feature", f"origin/{self.default_branch}")

    def enter(self, path: Path) -> None:
        """Change into ``path`` and keep the context's recorded directory in step."""
        os.chdir(path)
        self.runtime.context.cwd = Path.cwd()

    def run_command(self, command, *args, **kwargs):
        """Run a command with captured streams."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            command(*args, **kwargs)
        return stdout.getvalue(), stderr.getvalue()

    def test_remove__recovers_in_the_project_dir_and_requests_the_change(self):
        directive = self.temp_path / "cd-directive"
        directive.write_text("", encoding="utf-8")
        self.enter(self.project_dir / "feature")

        with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(directive)}):
            self.run_command(RemoveCommand(self.runtime), "feature", yes=True)

        self.assertEqual(f"{self.project_dir}\n", directive.read_text(encoding="utf-8"))
        self.assertEqual(self.project_dir, Path.cwd())
        self.assertEqual(self.project_dir, self.runtime.context.cwd)

    def test_remove__writes_a_shell_escaped_warning_when_the_directive_is_unset(self):
        self.enter(self.project_dir / "feature")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(CD_FILE_ENV_VAR, None)
            _, stderr = self.run_command(RemoveCommand(self.runtime), "feature", yes=True)

        self.assertIn(f"cd -- {shlex.quote(str(self.project_dir))}", stderr)
        self.assertNotIn("~/", stderr)
        self.assertEqual(self.project_dir, Path.cwd())

    def test_prune__recovers_in_the_project_dir_and_requests_the_change(self):
        directive = self.temp_path / "cd-directive"
        directive.write_text("", encoding="utf-8")
        head = self.runtime.git.get_branch_head("feature")
        self.runtime.gh.merged_pr_by_head.return_value = [
            {"number": 7, "title": "Feature", "url": "https://example.com/7", "headRefOid": head}
        ]
        self.enter(self.project_dir / "feature")

        with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(directive)}):
            self.run_command(PruneCommand(self.runtime), yes=True)

        self.assertEqual(f"{self.project_dir}\n", directive.read_text(encoding="utf-8"))
        self.assertEqual(self.project_dir, Path.cwd())


class ShellInitIntegrationTestCase(LocalRepoIntegrationTestCase):
    """Runs the emitted bash integration in a live ``bash`` process."""

    def setUp(self):
        super().setUp()
        self.runtime.git.fetch()
        self.runtime.git.add_worktree("subpath/feature", f"origin/{self.default_branch}")
        self.runtime.git.add_worktree("125", f"origin/{self.default_branch}")

    def shell_init(self, argv0: str = "treefort", shell: str = "bash") -> str:
        """Return the emitted integration script for the given invoked command name."""
        stdout = io.StringIO()
        with patch("sys.argv", [argv0]), redirect_stdout(stdout):
            ShellInitCommand(None)(shell)
        return stdout.getvalue()

    def run_bash(self, program: str, cwd: Path) -> subprocess.CompletedProcess:
        """Run a bash program in the isolated environment."""
        if not shutil.which("bash"):
            self.skipTest("bash is not installed")
        return subprocess.run(
            ["bash", "-c", program],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={**os.environ},
        )

    def complete(self, script: str, words: list[str], cword: int, cwd: Path | None = None):
        """Return the candidates the generated bash completion offers."""
        quoted = " ".join(shlex.quote(word) for word in words)
        program = (
            f"{script}\n"
            f"COMP_WORDS=({quoted})\n"
            f"COMP_CWORD={cword}\n"
            "_treefort_complete\n"
            'printf "%s\\n" "${COMPREPLY[@]}"\n'
        )
        result = self.run_bash(program, cwd or self.project_dir)
        self.assertEqual("", result.stderr, result.stderr)
        return sorted(entry for entry in result.stdout.splitlines() if entry)

    def test_shell_init__completes_subcommands_aliases_and_flags(self):
        candidates = self.complete(self.shell_init(), ["treefort", ""], 1)

        for expected in ("switch", "shell-init", "sw", "rm", "install", "--help", "--verbose"):
            with self.subTest(expected=expected):
                self.assertIn(expected, candidates)

    def test_shell_init__completes_switch_shortcuts_and_worktrees(self):
        candidates = self.complete(self.shell_init(), ["treefort", "sw", ""], 2)

        for expected in ("-", "^", "@", "subpath/feature", "125"):
            with self.subTest(expected=expected):
                self.assertIn(expected, candidates)

    def test_shell_init__completes_switch_flags(self):
        candidates = self.complete(self.shell_init(), ["treefort", "switch", "-"], 2)

        self.assertIn("--help", candidates)

    def test_shell_init__completes_branch_refs_and_remotes(self):
        script = self.shell_init()

        branches = self.complete(script, ["treefort", "checkout", ""], 2)
        remotes = self.complete(script, ["treefort", "checkout", "--remote", ""], 3)

        self.assertIn(f"origin/{self.default_branch}", branches)
        self.assertIn(self.default_branch, branches)
        self.assertIn("origin", remotes)

    def test_shell_init__completes_under_a_symlinked_alias(self):
        alias_dir = self.temp_path / "bin"
        alias_dir.mkdir()
        alias = alias_dir / "tf"
        alias.symlink_to(self.temp_path / "treefort")

        script = self.shell_init(argv0=str(alias))
        candidates = self.complete(script, ["tf", ""], 1)

        self.assertIn("tf() {", script)
        self.assertIn("switch", candidates)
        self.assertIn("sw", candidates)

    def test_shell_init__stays_silent_outside_a_repository(self):
        result = self.run_bash(
            f"{self.shell_init()}\n_treefort_worktrees\n_treefort_branch_refs\n_treefort_remotes\n",
            self.temp_path,
        )

        self.assertEqual("", result.stdout)
        self.assertEqual("", result.stderr)

    def test_shell_init__wrapper_changes_directory_and_returns_the_status(self):
        stub_dir = self.temp_path / "stub"
        stub_dir.mkdir()
        target = self.project_dir / "125"
        stub = stub_dir / "tfcmd"
        stub.write_text(
            f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(str(target))} > \"$TREEFORT_CD_FILE\"\n"
        )
        stub.chmod(0o700)
        script = self.shell_init(argv0=str(stub))
        program = (
            f'{script}\nPATH={shlex.quote(str(stub_dir))}:$PATH\ntfcmd\nprintf "cwd=%s\\n" "$PWD"\n'
        )

        result = self.run_bash(program, self.project_dir)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"cwd={target}", result.stdout)
