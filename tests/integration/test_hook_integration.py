"""Offline integration coverage for hook execution.

Every test runs a real hook script through the local-Git fixture in
:mod:`tests.integration.base`, so no test reaches the network, the developer's ``HOME``, or the
developer's Git configuration. The unit tests mock ``stream_exec``, so these tests are what prove
the environment actually crosses the process boundary.
"""

import contextlib
import hashlib
import io
import os
from unittest import mock

from treefort.commands.hook import HookCommand
from treefort.commands.remove import RemoveCommand
from treefort.git import GitUri

from .base import LocalRepoIntegrationTestCase

# Prints every exported value and every positional argument, one per line, so a test can assert
# on the whole contract at once. ``${NAME-unset}`` distinguishes an unset variable from an empty
# one, which a bare ``$NAME`` cannot.
HOOK_SCRIPT = """#!/bin/sh
{{
  printf '%s\\n' "${{TREEFORT_HOOK-unset}}"
  printf '%s\\n' "${{TREEFORT_REPO_NAME-unset}}"
  printf '%s\\n' "${{TREEFORT_REPO_DIR-unset}}"
  printf '%s\\n' "${{TREEFORT_DEFAULT_BRANCH-unset}}"
  printf '%s\\n' "${{TREEFORT_WORKTREE_NAME-unset}}"
  printf '%s\\n' "${{TREEFORT_WORKTREE_NAME_NORMALIZED-unset}}"
  printf '%s\\n' "${{TREEFORT_WORKTREE_DIR-unset}}"
  printf '%s\\n' "${{TREEFORT_BASE_REF-unset}}"
  printf '%s\\n' "${{1-unset}}"
  printf '%s\\n' "${{2-unset}}"
  printf '%s\\n' "${{3-unset}}"
}} > '{output}'
"""


class HookEnvironmentIntegrationTestCase(LocalRepoIntegrationTestCase):
    """Exercises the hook environment and ``--no-hooks`` against real subprocesses."""

    def write_hook(self, name, output_file):
        """Install an executable project hook that records its arguments and environment."""
        hook_file = self.project_dir / ".treefort" / "hooks" / name
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        hook_file.write_text(HOOK_SCRIPT.format(output=output_file), encoding="utf-8")
        hook_file.chmod(0o755)
        return hook_file

    def test_hook_command__exports_the_environment_a_real_hook_observes(self):
        output_file = self.temp_path / "post-create.txt"
        self.write_hook("post_create", output_file)
        project_dir = self.project_dir.resolve()

        HookCommand(self.runtime)("post_create", "feature/one", yes=True)

        self.assertEqual(
            [
                "post_create",
                self.repo_name,
                str(project_dir),
                self.default_branch,
                "feature/one",
                "feature-one",
                str(project_dir / "feature/one"),
                f"origin/{self.default_branch}",
                "feature/one",
                f"origin/{self.default_branch}",
                "feature-one",
            ],
            output_file.read_text(encoding="utf-8").splitlines(),
        )

    def test_hook_command__checkout_hooks_receive_the_branch_ref_checkout_passes(self):
        # `treefort checkout feature/one` passes "origin/feature/one", not the default branch
        output_file = self.temp_path / "pre-checkout.txt"
        self.write_hook("pre_checkout", output_file)

        HookCommand(self.runtime)("pre_checkout", "feature/one", yes=True)

        lines = output_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual("origin/feature/one", lines[7])  # TREEFORT_BASE_REF
        self.assertEqual("origin/feature/one", lines[9])  # $2

    def test_hook_command__pre_init_leaves_post_clone_variables_unset(self):
        output_file = self.temp_path / "pre-init.txt"
        hook_file = self.home_dir / ".treefort" / "hooks" / "pre_init"
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        hook_file.write_text(HOOK_SCRIPT.format(output=output_file), encoding="utf-8")
        hook_file.chmod(0o755)

        HookCommand(self.runtime)("pre_init", yes=True)

        lines = output_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual("pre_init", lines[0])
        # `init` passes the URI it clones, so `treefort hook` passes the one `origin` records.
        # The fixture's origin is a local path, which `GitUri` reports as invalid, so the
        # derived name follows its local-path rule rather than `repo_name`.
        self.assertEqual(str(self.origin_dir), lines[8])
        self.assertEqual(GitUri(str(self.origin_dir)).name, lines[1])
        self.assertEqual(str(self.project_dir.resolve()), lines[2])
        # The clone does not exist yet, so these are unset rather than empty
        self.assertEqual(["unset"] * 5, lines[3:8])

    def test_hook_command__withholds_a_trefort_variable_left_in_the_environment(self):
        # A hook that runs `treefort remove`, or a hook author who exports the variables to try
        # a script by hand, leaves its own context in the caller's environment. The hook below
        # must still see those slots as unset.
        output_file = self.temp_path / "pre-init-leak.txt"
        hook_file = self.home_dir / ".treefort" / "hooks" / "pre_init"
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        hook_file.write_text(HOOK_SCRIPT.format(output=output_file), encoding="utf-8")
        hook_file.chmod(0o755)

        leaked = {
            "TREEFORT_WORKTREE_NAME": "leaked-from-outer-hook",
            "TREEFORT_BASE_REF": "origin/leaked",
        }
        with mock.patch.dict(os.environ, leaked):
            HookCommand(self.runtime)("pre_init", yes=True)

        lines = output_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(["unset"] * 5, lines[3:8])

    def test_hook_command__passes_the_standard_variables_and_nothing_else(self):
        # A hook needs enough of the caller's environment to find its tools, and nothing more
        output_file = self.temp_path / "env.txt"
        hook_file = self.home_dir / ".treefort" / "hooks" / "pre_init"
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        hook_file.write_text(
            "#!/bin/sh\n"
            f'printf \'%s\\n\' "${{PATH-unset}}" "${{HOME-unset}}" '
            f"\"${{GITHUB_TOKEN-unset}}\" > '{output_file}'\n",
            encoding="utf-8",
        )
        hook_file.chmod(0o755)

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"}):
            HookCommand(self.runtime)("pre_init", yes=True)

        path, home, token = output_file.read_text(encoding="utf-8").splitlines()
        self.assertNotEqual("unset", path)
        self.assertEqual(str(self.home_dir), home)
        self.assertEqual("unset", token)

    def test_hook_command__list_reports_the_real_approval_status(self):
        approved = self.write_hook("post_create", self.temp_path / "a.txt")
        modified = self.write_hook("post_remove", self.temp_path / "b.txt")
        self.write_hook("pre_remove", self.temp_path / "c.txt")
        allowed_hooks = self.runtime.context.get_global_config().allowed_hooks
        allowed_hooks.update({str(approved): hashlib.sha256(approved.read_bytes()).hexdigest()})
        allowed_hooks.update({str(modified): "stale-checksum"})
        self.runtime.context.set_config(allowed_hooks)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            HookCommand(self.runtime)()

        rows = [line.split() for line in output.getvalue().splitlines()[1:] if line.split()]
        self.assertEqual(
            {
                "post_create": "approved",
                "post_remove": "modified",
                "pre_remove": "unapproved",
            },
            {row[0]: row[3] for row in rows},
        )

    def test_remove__no_hooks_skips_a_real_hook_without_prompting(self):
        output_file = self.temp_path / "pre-remove.txt"
        hook_file = self.write_hook("pre_remove", output_file)
        self.runtime.git.add_worktree("wt-one", f"origin/{self.default_branch}")

        # The hook is executable and would prompt for approval, so reaching it here would raise
        # on the captured stdin rather than silently pass
        self.assertTrue(hook_file.stat().st_mode & 0o111)
        RemoveCommand(self.runtime)("wt-one", no_hooks=True)

        self.assertFalse(output_file.exists())
        self.assertFalse((self.project_dir / "wt-one").exists())
