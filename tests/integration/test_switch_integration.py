"""Integration tests for worktree switching and the generated shell integration script."""

import os
import subprocess
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from treefort.commands.shell_init import ShellInitCommand
from treefort.commands.switch import SwitchCommand
from treefort.main import WorktreeCommands

from .base import LocalRepoIntegrationTestCase


class SwitchIntegrationTestCase(LocalRepoIntegrationTestCase):
    def setUp(self):
        super().setUp()
        self.cd_file = self.temp_path / "cd-file"

    def switch(self, target: str, cwd: Path | None = None) -> str:
        """Run `switch` from the given directory and return the requested target path."""
        if cwd is not None:
            self.runtime.context.cwd = cwd
        with patch.dict(os.environ, {"TREEFORT_CD_FILE": str(self.cd_file)}):
            SwitchCommand(self.runtime)(target)
        return self.cd_file.read_text(encoding="utf-8").strip()

    def test_switch__writes_cd_directive_and_toggles_between_worktrees(self):
        self.runtime.git.add_worktree("feature-a", "main")
        self.runtime.git.add_worktree("sub/feature", "main")
        config = self.runtime.context.get_config()

        self.assertEqual(self.switch("feature-a"), str(self.project_dir / "feature-a"))
        self.assertIsNone(config.last_worktree)

        self.assertEqual(
            self.switch("sub/feature", cwd=self.project_dir / "feature-a"),
            str(self.project_dir / "sub" / "feature"),
        )
        self.assertEqual(config.last_worktree, "feature-a")

        self.assertEqual(
            self.switch("-", cwd=self.project_dir / "sub" / "feature"),
            str(self.project_dir / "feature-a"),
        )
        self.assertEqual(config.last_worktree, "sub/feature")

        self.assertEqual(
            self.switch("-", cwd=self.project_dir / "feature-a"),
            str(self.project_dir / "sub" / "feature"),
        )
        self.assertEqual(config.last_worktree, "feature-a")


class ShellInitIntegrationTestCase(LocalRepoIntegrationTestCase):
    def bash_script(self) -> str:
        """The generated bash integration script, built against the fixture project."""
        cli = WorktreeCommands()
        stdout = StringIO()
        with redirect_stdout(stdout):
            ShellInitCommand(cli._runtime, cli)("bash")
        return stdout.getvalue()

    def completions(self, script: str, words: list[str]) -> list[str]:
        result = self.run_completion_script(script, words, cwd=self.project_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split()

    def run_completion_script(
        self, script: str, words: list[str], cwd: Path
    ) -> subprocess.CompletedProcess:
        """Execute the generated completion function for the given command line."""
        word_literals = " ".join(f"'{word}'" for word in words)
        driver = (
            f"{script}\n"
            f"COMP_WORDS=({word_literals})\n"
            f"COMP_CWORD={len(words) - 1}\n"
            "_complete-treefort\n"
            "printf '%s\\n' \"${COMPREPLY[@]}\"\n"
        )
        return subprocess.run(
            ["bash", "-c", driver],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_bash_completion__offers_worktrees_refs_and_remotes(self):
        self.runtime.git.add_worktree("feature-a", "main")
        self.runtime.git.add_worktree("sub/feature", "main")
        script = self.bash_script()

        switch_completions = self.completions(script, ["treefort", "switch", ""])
        self.assertIn("feature-a", switch_completions)
        self.assertIn("sub/feature", switch_completions)
        self.assertIn("-", switch_completions)
        self.assertIn("^", switch_completions)
        self.assertIn("@", switch_completions)

        checkout_completions = self.completions(script, ["treefort", "checkout", ""])
        self.assertIn("main", checkout_completions)
        self.assertIn("origin/main", checkout_completions)

        self.assertIn("origin", self.completions(script, ["treefort", "checkout", "--remote", ""]))

        create_completions = self.completions(script, ["treefort", "create", "my-worktree", ""])
        self.assertIn("feature-a", create_completions)
        self.assertIn("origin/main", create_completions)
        self.assertIn("@", create_completions)

        subcommand_completions = self.completions(script, ["treefort", ""])
        for subcommand in ("checkout", "create", "switch", "shell-init", "remove", "rm", "sw"):
            self.assertIn(subcommand, subcommand_completions)

    def test_bash_completion__counts_positionals_after_option_values(self):
        self.runtime.git.add_worktree("feature-a", "main")
        script = self.bash_script()

        # --remote consumes a value, so the branch still completes as the first positional
        completions = self.completions(script, ["treefort", "checkout", "--remote", "origin", ""])

        self.assertIn("main", completions)
        self.assertIn("origin/main", completions)

    def test_bash_completion__completes_flags_after_positional_arguments(self):
        self.runtime.git.add_worktree("feature-a", "main")
        script = self.bash_script()

        after_positional = self.completions(script, ["treefort", "checkout", "main", "--"])
        self.assertIn("--remote", after_positional)
        self.assertIn("--yes", after_positional)

        after_global = self.completions(script, ["treefort", "checkout", "--verbose", "--"])
        self.assertIn("--remote", after_global)
        self.assertIn("--yes", after_global)

    def test_bash_completion__completes_alias_flags_and_worktrees(self):
        self.runtime.git.add_worktree("feature-a", "main")
        script = self.bash_script()

        self.assertIn("--force", self.completions(script, ["treefort", "rm", "--"]))
        self.assertIn("feature-a", self.completions(script, ["treefort", "sw", ""]))

    def test_bash_completion__is_silent_outside_git_repos(self):
        script = self.bash_script()

        result = self.run_completion_script(script, ["treefort", "checkout", ""], cwd=self.home_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.split(), [])

    def test_bash_completion__terminates_when_cwd_is_deleted(self):
        script = self.bash_script()
        gone = self.temp_path / "gone"
        gone.mkdir()
        driver = (
            f"{script}\n"
            f"cd '{gone}' && rmdir '{gone}' || exit 9\n"
            'COMP_WORDS=(treefort switch "")\n'
            "COMP_CWORD=2\n"
            "_complete-treefort\n"
            "printf '%s\\n' \"${COMPREPLY[@]}\"\n"
        )

        # treefort remove can delete the directory the user is standing in; completion must
        # not spin on an empty `pwd -P` result or print errors
        result = subprocess.run(
            ["bash", "-c", driver],
            cwd=str(self.project_dir),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("-", result.stdout.split())
