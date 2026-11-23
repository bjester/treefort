import inspect
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from unittest import TestCase
from unittest.mock import Mock, patch

from treefort.command import Arg, Completion
from treefort.commands.hook import HookCommand
from treefort.errors import (
    AncestorNotFoundError,
    HookError,
    ProjectNotFoundError,
    WorktreeNotFoundError,
)
from treefort.hooks import (
    POST_CREATE,
    POST_INIT,
    POST_REMOVE,
    PRE_CHECKOUT,
    PRE_CREATE,
    PRE_INIT,
    PRE_REMOVE,
    ApprovalStatus,
)


class StubContext:
    """Context double that records the order of the calls the command must make."""

    def __init__(
        self,
        project_dir,
        config,
        global_config,
        global_config_dir,
        events,
        worktree_name=None,
        in_project=True,
    ):
        self.project_dir = project_dir
        self._config = config
        self._global_config = global_config
        self._global_config_dir = global_config_dir
        self._events = events
        self._worktree_name = worktree_name
        self.in_project = in_project
        self.assert_called = False
        self.use_calls = []
        self.global_config_calls = 0

    @property
    def config_dir(self):
        if not self.in_project:
            raise AncestorNotFoundError("no project")
        return self.project_dir / ".treefort"

    @property
    def global_config_dir(self):
        return self._global_config_dir

    def assert_within_project(self):
        self.assert_called = True
        self._events.append("assert")
        if not self.in_project:
            raise ProjectNotFoundError("Project not found")

    def get_config(self):
        return self._config

    def get_global_config(self):
        self.global_config_calls += 1
        return self._global_config

    def current_worktree_name(self):
        self._events.append("current_worktree_name")
        return self._worktree_name

    @contextmanager
    def use(self, cwd, *, end_at=None):
        self.use_calls.append((cwd, end_at))
        self._events.append("use")
        yield


class HookCommandTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        self.project_dir = self.tmp_path / "project"
        self.project_dir.mkdir()
        self.project_hooks_dir = self.project_dir / ".treefort" / "hooks"
        self.project_hooks_dir.mkdir(parents=True)

        self.global_config_dir = self.tmp_path / "home" / ".treefort"
        self.global_hooks_dir = self.global_config_dir / "hooks"
        self.global_hooks_dir.mkdir(parents=True)

        self.config = SimpleNamespace(url="https://github.com/octo/repo", default_branch="main")
        self.global_config = SimpleNamespace()

        self.events = []
        self.context = StubContext(
            self.project_dir,
            self.config,
            self.global_config,
            self.global_config_dir,
            self.events,
            worktree_name="feature",
        )

        self.hooks = SimpleNamespace(
            fire=Mock(return_value=True),
            approval_status=Mock(return_value=ApprovalStatus.UNAPPROVED),
        )
        self.logger = Mock()
        self.runtime = SimpleNamespace(
            context=self.context,
            hooks=self.hooks,
            logger=self.logger,
            get_default_remote=Mock(return_value=SimpleNamespace(name="origin")),
        )
        self.command = HookCommand(self.runtime)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def write_hook(self, hooks_dir, name, mode=0o755, content=b"#!/bin/sh\necho ok\n"):
        hook_file = hooks_dir / name
        hook_file.write_bytes(content)
        hook_file.chmod(mode)
        return hook_file

    def listed_rows(self):
        """Run list mode and return the rows handed to the table renderer."""
        with patch("treefort.commands.hook.ui.format_table") as format_table:
            format_table.return_value = "TABLE"
            with patch("builtins.print") as mock_print:
                self.command()

        format_table.assert_called_once()
        self.assertEqual("TABLE", mock_print.call_args.args[0])
        headers, rows = format_table.call_args.args
        self.assertEqual(["HOOK", "SCOPE", "EXECUTABLE", "STATUS", "PATH"], headers)
        return rows

    def test_call__declares_the_cli_contract(self):
        parameters = inspect.signature(HookCommand.__call__).parameters

        self.assertEqual(
            (str | None, Arg(complete=Completion.HOOK)),
            get_args(parameters["hook_name"].annotation),
        )
        self.assertEqual(
            (str | None, Arg(complete=Completion.WORKTREE)),
            get_args(parameters["worktree_name"].annotation),
        )
        self.assertEqual(
            (str | None, Arg(complete=Completion.BASE_REF)),
            get_args(parameters["base_ref"].annotation),
        )
        self.assertEqual((bool, Arg(short="-y")), get_args(parameters["yes"].annotation))

        self.assertIs(inspect.Parameter.POSITIONAL_OR_KEYWORD, parameters["hook_name"].kind)
        self.assertIs(inspect.Parameter.POSITIONAL_OR_KEYWORD, parameters["worktree_name"].kind)
        self.assertIs(inspect.Parameter.KEYWORD_ONLY, parameters["base_ref"].kind)
        self.assertIs(inspect.Parameter.KEYWORD_ONLY, parameters["yes"].kind)

        for name in ("hook_name", "worktree_name", "base_ref"):
            self.assertIsNone(parameters[name].default)
        self.assertIs(False, parameters["yes"].default)

    def test_call__lists_global_and_project_hooks(self):
        global_hook = self.write_hook(self.global_hooks_dir, "post_init", 0o755)
        project_hook = self.write_hook(self.project_hooks_dir, "pre_create", 0o644)
        self.hooks.approval_status.side_effect = lambda path: (
            ApprovalStatus.APPROVED if path == global_hook else ApprovalStatus.UNAPPROVED
        )

        rows = self.listed_rows()

        self.assertEqual(
            [
                ["post_init", "global", "yes", "approved", str(global_hook)],
                ["pre_create", "project", "no", "unapproved", str(project_hook)],
            ],
            rows,
        )

    def test_call__renders_the_approval_status_for_each_hook(self):
        hook_file = self.write_hook(self.project_hooks_dir, "post_remove")
        self.hooks.approval_status.return_value = ApprovalStatus.MODIFIED

        rows = self.listed_rows()

        self.assertEqual(["post_remove", "project", "yes", "modified", str(hook_file)], rows[0])

    def test_call__ignores_files_and_directories_that_are_not_lifecycle_hooks(self):
        self.write_hook(self.project_hooks_dir, "custom_hook")
        (self.project_hooks_dir / "pre_remove").mkdir()
        real_hook = self.write_hook(self.project_hooks_dir, "post_init")

        rows = self.listed_rows()

        self.assertEqual([["post_init", "project", "yes", "unapproved", str(real_hook)]], rows)

    def test_call__lists_only_global_hooks_outside_a_project(self):
        global_hook = self.write_hook(self.global_hooks_dir, "pre_init")
        self.context.in_project = False

        rows = self.listed_rows()

        self.assertEqual([["pre_init", "global", "yes", "unapproved", str(global_hook)]], rows)

    def test_call__logs_when_no_hooks_are_configured(self):
        with patch("builtins.print") as mock_print:
            self.command()

        mock_print.assert_not_called()
        self.logger.info.assert_called_once_with("No hooks configured.")
        # A read-only listing must not resolve the global configuration when no hook exists
        self.assertEqual(0, self.context.global_config_calls)

    def test_call__list_alias_lists_hooks(self):
        self.command("list")

        self.hooks.fire.assert_not_called()
        self.logger.info.assert_called_once_with("No hooks configured.")

    def test_call__raises_for_an_unknown_hook_name(self):
        with self.assertRaisesRegex(HookError, "Unknown hook 'nope'"):
            self.command("nope")

        self.assertFalse(self.context.assert_called)
        self.hooks.fire.assert_not_called()

    def test_call__raises_outside_a_project(self):
        self.context.in_project = False

        with self.assertRaises(ProjectNotFoundError):
            self.command("post_init")

        self.hooks.fire.assert_not_called()

    def test_call__runs_pre_init_with_the_repository_uri_and_project_dir(self):
        self.command("pre_init")

        self.hooks.fire.assert_called_once_with(
            PRE_INIT,
            "https://github.com/octo/repo",
            self.project_dir,
            bypass_allowlist=False,
        )

    def test_call__runs_post_init_with_the_repository_uri_and_project_dir(self):
        self.command("post_init")

        self.hooks.fire.assert_called_once_with(
            POST_INIT,
            "https://github.com/octo/repo",
            self.project_dir,
            bypass_allowlist=False,
        )
        self.assertEqual([(self.project_dir, None)], self.context.use_calls)

    def test_call__runs_a_create_hook_with_the_default_base_ref(self):
        self.command("post_create", "feature/one")

        self.runtime.get_default_remote.assert_called_once_with()
        self.hooks.fire.assert_called_once_with(
            POST_CREATE,
            "feature/one",
            "origin/main",
            "feature-one",
            bypass_allowlist=False,
        )

    def test_call__runs_a_checkout_hook_with_an_explicit_base_ref(self):
        self.command("pre_checkout", "feature", base_ref="upstream/release")

        self.runtime.get_default_remote.assert_not_called()
        self.hooks.fire.assert_called_once_with(
            PRE_CHECKOUT,
            "feature",
            "upstream/release",
            "feature",
            bypass_allowlist=False,
        )

    def test_call__falls_back_to_origin_when_no_default_remote_matches(self):
        self.runtime.get_default_remote.return_value = None

        self.command("pre_create", "feature")

        self.hooks.fire.assert_called_once_with(
            PRE_CREATE,
            "feature",
            "origin/main",
            "feature",
            bypass_allowlist=False,
        )

    def test_call__runs_a_remove_hook_without_a_base_ref(self):
        self.command("pre_remove", "feature")

        self.hooks.fire.assert_called_once_with(
            PRE_REMOVE,
            "feature",
            "feature",
            bypass_allowlist=False,
        )

    def test_call__infers_the_current_worktree_before_entering_the_project_dir(self):
        self.command("post_remove")

        self.hooks.fire.assert_called_once_with(
            POST_REMOVE,
            "feature",
            "feature",
            bypass_allowlist=False,
        )
        self.assertEqual(["assert", "current_worktree_name", "use"], self.events)

    def test_call__raises_when_a_worktree_hook_has_no_worktree(self):
        self.context._worktree_name = None

        with self.assertRaises(WorktreeNotFoundError):
            self.command("pre_remove")

        self.hooks.fire.assert_not_called()

    def test_call__yes_true_bypasses_the_allowlist(self):
        self.command("post_init", yes=True)

        self.hooks.fire.assert_called_once_with(
            POST_INIT,
            "https://github.com/octo/repo",
            self.project_dir,
            bypass_allowlist=True,
        )

    def test_call__runs_a_checkout_hook_with_the_branch_ref_checkout_passes(self):
        # `treefort checkout feature/one` passes "origin/feature/one", not the default branch
        self.command("pre_checkout", "feature/one")

        self.hooks.fire.assert_called_once_with(
            PRE_CHECKOUT,
            "feature/one",
            "origin/feature/one",
            "feature-one",
            bypass_allowlist=False,
        )

    def test_call__logs_when_no_hook_ran(self):
        self.hooks.fire.return_value = False

        self.command("post_init")

        self.logger.info.assert_called_once_with("No post_init hook ran.")

    def test_call__stays_quiet_when_a_hook_ran(self):
        self.hooks.fire.return_value = True

        self.command("post_init")

        self.logger.info.assert_not_called()
