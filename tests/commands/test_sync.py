import inspect
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from unittest import TestCase
from unittest.mock import Mock, patch

from treefort.command import Arg, Completion
from treefort.commands.sync import SyncCommand
from treefort.errors import ProjectNotFoundError


class StubContext:
    def __init__(self, project_dir, config):
        self.project_dir = project_dir
        self._config = config
        self.assert_called = False
        self.use_calls = []
        self.events = []

    def assert_within_project(self):
        self.assert_called = True

    def get_config(self):
        return self._config

    @contextmanager
    def use(self, cwd, *, end_at=None):
        self.use_calls.append((cwd, end_at))
        self.events.append("enter")
        try:
            yield
        finally:
            self.events.append("exit")


class SyncCommandTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.project_dir = self.tmp_path / "project"
        self.project_dir.mkdir()

        self.config = SimpleNamespace(default_branch="main")
        self.context = StubContext(self.project_dir, self.config)

        self.hooks = SimpleNamespace(sync_from_git=Mock(return_value=2))
        self.templates = SimpleNamespace(sync_from_git=Mock(return_value=3))
        self.git = SimpleNamespace(fetch=Mock())
        self.logger = Mock()

        self.runtime = SimpleNamespace(
            context=self.context,
            hooks=self.hooks,
            templates=self.templates,
            git=self.git,
            logger=self.logger,
            verbose=False,
            get_default_remote=Mock(return_value=SimpleNamespace(name="origin")),
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_call__declares_ref_as_an_optional_positional_with_branch_completion(self):
        parameter = inspect.signature(SyncCommand.__call__).parameters["ref"]

        self.assertIs(inspect.Parameter.POSITIONAL_OR_KEYWORD, parameter.kind)
        self.assertIsNone(parameter.default)
        self.assertEqual(
            (str | None, Arg(complete=Completion.BRANCH_REF)), get_args(parameter.annotation)
        )

    def test_call__declares_fetch_as_a_keyword_only_flag_defaulting_true(self):
        parameter = inspect.signature(SyncCommand.__call__).parameters["fetch"]

        self.assertIs(inspect.Parameter.KEYWORD_ONLY, parameter.kind)
        self.assertIs(True, parameter.default)
        self.assertIs(bool, parameter.annotation)

    def test_call__asserts_within_project(self):
        SyncCommand(self.runtime)()

        self.assertTrue(self.context.assert_called)

    def test_call__propagates_project_not_found(self):
        self.context.assert_within_project = Mock(side_effect=ProjectNotFoundError("Project"))

        with self.assertRaises(ProjectNotFoundError):
            SyncCommand(self.runtime)()

        self.git.fetch.assert_not_called()

    def test_call__enters_the_project_directory(self):
        SyncCommand(self.runtime)()

        self.assertEqual([(self.project_dir, None)], self.context.use_calls)

    def test_call__syncs_the_default_remote_branch(self):
        SyncCommand(self.runtime)()

        self.git.fetch.assert_called_once_with("origin")
        self.hooks.sync_from_git.assert_called_once_with(
            self.runtime.git, "origin/main", overwrite=True
        )
        self.templates.sync_from_git.assert_called_once_with(
            self.runtime.git, "origin/main", overwrite=True
        )

    def test_call__falls_back_to_origin_without_a_default_remote(self):
        self.runtime.get_default_remote.return_value = None

        SyncCommand(self.runtime)()

        self.git.fetch.assert_called_once_with("origin")
        self.hooks.sync_from_git.assert_called_once_with(
            self.runtime.git, "origin/main", overwrite=True
        )

    def test_call__uses_the_explicit_ref(self):
        SyncCommand(self.runtime)(ref="upstream/release")

        self.hooks.sync_from_git.assert_called_once_with(
            self.runtime.git, "upstream/release", overwrite=True
        )
        self.templates.sync_from_git.assert_called_once_with(
            self.runtime.git, "upstream/release", overwrite=True
        )

    def test_call__no_fetch_skips_the_fetch(self):
        SyncCommand(self.runtime)(fetch=False)

        self.git.fetch.assert_not_called()
        self.hooks.sync_from_git.assert_called_once_with(
            self.runtime.git, "origin/main", overwrite=True
        )

    def test_call__spins_while_fetching(self):
        with patch("treefort.commands.sync.ui.spinner") as spinner:
            SyncCommand(self.runtime)()

        spinner.assert_called_once_with("Fetching origin...", enabled=True)

    def test_call__disables_the_spinner_when_verbose(self):
        self.runtime.verbose = True

        with patch("treefort.commands.sync.ui.spinner") as spinner:
            SyncCommand(self.runtime)()

        spinner.assert_called_once_with("Fetching origin...", enabled=False)

    def test_call__fetches_before_syncing(self):
        self.context.events = []
        self.git.fetch.side_effect = lambda *args, **kwargs: self.context.events.append("fetch")
        self.hooks.sync_from_git.side_effect = lambda *args, **kwargs: (
            self.context.events.append("hooks") or 2
        )
        self.templates.sync_from_git.side_effect = lambda *args, **kwargs: (
            self.context.events.append("templates") or 3
        )

        SyncCommand(self.runtime)()

        self.assertEqual(["enter", "fetch", "hooks", "templates", "exit"], self.context.events)

    def test_call__logs_the_sync_summary(self):
        with patch("treefort.ui.is_color_enabled", return_value=False):
            SyncCommand(self.runtime)()

        self.logger.info.assert_called_once_with(
            "✓ Synced 2 hook(s) and 3 template(s) from origin/main"
        )
