from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from treefort.commands.create import CreateCommand
from treefort.git import GitRemote
from treefort.hooks import Hook


class StubContext:
    def __init__(self, project_dir, config):
        self.project_dir = project_dir
        self._config = config
        self.assert_called = False

    def assert_within_project(self):
        self.assert_called = True

    def get_config(self):
        return self._config

    @contextmanager
    def use(self, cwd):
        yield


class CreateCommandTestCase(TestCase):
    def setUp(self):
        self.config = SimpleNamespace(default_branch="main")
        self.context = StubContext(Path("/repo"), self.config)
        self.hooks = SimpleNamespace(fire=Mock())
        self.git = SimpleNamespace(fetch=Mock(), add_worktree=Mock())
        self.templates = SimpleNamespace(copy=Mock())
        self.runtime = SimpleNamespace(
            logger=Mock(),
            context=self.context,
            hooks=self.hooks,
            git=self.git,
            templates=self.templates,
            get_remote=Mock(return_value=GitRemote("origin", "uri", "fetch")),
            get_default_remote=Mock(return_value=GitRemote("upstream", "uri", "fetch")),
        )
        self.command = CreateCommand(self.runtime)

    def test_call__uses_default_branch_and_remote(self):
        self.command("feature")

        self.assertTrue(self.context.assert_called)
        self.runtime.get_default_remote.assert_called_once_with()
        self.git.fetch.assert_called_once_with("upstream")
        self.git.add_worktree.assert_called_once_with("feature", "upstream/main")
        self.templates.copy.assert_called_once_with("feature")
        self.hooks.fire.assert_any_call(
            Hook.pre_create,
            "feature",
            "upstream/main",
            "feature",
            bypass_allowlist=False,
        )
        self.hooks.fire.assert_any_call(
            Hook.post_create,
            "feature",
            "upstream/main",
            "feature",
            bypass_allowlist=False,
        )

    def test_call__uses_default_remote(self):
        self.command("feature", "release-x")

        self.assertTrue(self.context.assert_called)
        self.runtime.get_default_remote.assert_called_once_with()
        self.git.fetch.assert_called_once_with("upstream")
        self.git.add_worktree.assert_called_once_with("feature", "upstream/release-x")
        self.templates.copy.assert_called_once_with("feature")
        self.hooks.fire.assert_any_call(
            Hook.pre_create,
            "feature",
            "upstream/release-x",
            "feature",
            bypass_allowlist=False,
        )
        self.hooks.fire.assert_any_call(
            Hook.post_create,
            "feature",
            "upstream/release-x",
            "feature",
            bypass_allowlist=False,
        )

    def test_call__respects_explicit_remote_and_ref(self):
        self.command("feature", "alt/dev")

        self.assertTrue(self.context.assert_called)
        self.runtime.get_remote.assert_not_called()
        self.runtime.get_default_remote.assert_not_called()
        self.git.fetch.assert_called_once_with("alt")
        self.git.add_worktree.assert_called_once_with("feature", "alt/dev")
        self.templates.copy.assert_called_once_with("feature")
        self.hooks.fire.assert_any_call(
            Hook.pre_create,
            "feature",
            "alt/dev",
            "feature",
            bypass_allowlist=False,
        )
        self.hooks.fire.assert_any_call(
            Hook.post_create,
            "feature",
            "alt/dev",
            "feature",
            bypass_allowlist=False,
        )

    def test_call__handles_ref_with_slashes(self):
        self.command("feature", "upstream/some/nested/branch")

        self.assertTrue(self.context.assert_called)
        self.runtime.get_remote.assert_not_called()
        self.git.fetch.assert_called_once_with("upstream")
        self.git.add_worktree.assert_called_once_with("feature", "upstream/some/nested/branch")
        self.templates.copy.assert_called_once_with("feature")
        self.hooks.fire.assert_any_call(
            Hook.pre_create,
            "feature",
            "upstream/some/nested/branch",
            "feature",
            bypass_allowlist=False,
        )
        self.hooks.fire.assert_any_call(
            Hook.post_create,
            "feature",
            "upstream/some/nested/branch",
            "feature",
            bypass_allowlist=False,
        )

    def test_call__logs_completion_milestone(self):
        with patch("treefort.ui.is_color_enabled", return_value=False):
            self.command("feature")

        self.runtime.logger.info.assert_called_once_with(
            "✓ Created worktree feature (from main) at /repo/feature"
        )

    def test_call__spins_while_fetching(self):
        with patch("treefort.commands.create.ui.spinner") as spinner:
            self.command("feature")

        spinner.assert_called_once_with("Fetching upstream...", enabled=True)

    def test_call__disables_spinner_when_verbose(self):
        self.runtime.verbose = True

        with patch("treefort.commands.create.ui.spinner") as spinner:
            self.command("feature")

        spinner.assert_called_once_with("Fetching upstream...", enabled=False)

    def test_call__runs_hooks_outside_spinner(self):
        events = []
        self.hooks.fire.side_effect = lambda hook, *args, **kwargs: events.append(hook.name)

        with patch("treefort.commands.create.ui.spinner") as spinner:
            spinner.return_value.__exit__.side_effect = lambda *args: events.append("exit")
            self.command("feature")

        self.assertEqual(events, ["exit", "pre_create", "post_create"])

    def test_call__yes_true_sets_bypass_allowlist_true(self):
        self.command("feature", yes=True)

        self.hooks.fire.assert_any_call(
            Hook.pre_create,
            "feature",
            "upstream/main",
            "feature",
            bypass_allowlist=True,
        )
        self.hooks.fire.assert_any_call(
            Hook.post_create,
            "feature",
            "upstream/main",
            "feature",
            bypass_allowlist=True,
        )
