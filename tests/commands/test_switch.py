import inspect
import io
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from unittest import TestCase
from unittest.mock import Mock

from treefort.command import Arg, Completion
from treefort.commands.switch import SwitchCommand
from treefort.errors import ProjectNotFoundError, WorktreeNotFoundError


class StubConfig:
    """Repository configuration that records every persisted update."""

    def __init__(self, last_worktree=None, default_branch="main"):
        self.last_worktree = last_worktree
        self.default_branch = default_branch
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)
        for key, value in kwargs.items():
            setattr(self, key, value)


class StubContext:
    """Context recording directory change requests instead of touching the filesystem."""

    def __init__(self, project_dir, config):
        self.project_dir = project_dir
        self.cwd = project_dir
        self._config = config
        self.cd_requests = []
        self.cd_enabled = False
        self.cd_delivered = False
        self.saved_configs = []

    def assert_within_project(self):
        pass

    def get_config(self):
        return self._config

    def set_config(self, config):
        self.saved_configs.append(config)

    def is_cd_enabled(self):
        return self.cd_enabled

    def request_cd(self, target_dir):
        self.cd_requests.append(target_dir)
        return self.cd_delivered


class SwitchCommandTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.project_dir = self.tmp_path / "project"
        self.project_dir.mkdir()
        (self.project_dir / ".bare").mkdir()

        self.config = StubConfig()
        self.context = StubContext(self.project_dir, self.config)
        self.git = Mock()
        self.logger = Mock()
        self.runtime = SimpleNamespace(context=self.context, git=self.git, logger=self.logger)
        self.command = SwitchCommand(self.runtime)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def add_worktree(self, name, branch=None):
        """Create a worktree directory and return its ``git worktree list`` entry."""
        path = self.project_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return {"path": str(path), "branch": branch, "is_bare": False}

    def run_command(self, target):
        """Invoke the command, returning the captured stdout and stderr."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.command(target)
        return stdout.getvalue(), stderr.getvalue()

    def test_call__declares_the_cli_contract(self):
        parameters = inspect.signature(SwitchCommand.__call__).parameters

        self.assertEqual("switch", SwitchCommand._name)
        self.assertEqual(("sw",), tuple(SwitchCommand._aliases))
        self.assertIs(inspect.Parameter.POSITIONAL_OR_KEYWORD, parameters["target"].kind)
        self.assertEqual(
            (str, Arg(complete=Completion.WORKTREE_SWITCH)),
            get_args(parameters["target"].annotation),
        )

    def test_call__propagates_a_missing_project(self):
        self.context.assert_within_project = Mock(
            side_effect=ProjectNotFoundError("Project not found")
        )

        with self.assertRaises(ProjectNotFoundError):
            self.command("feature")

    def test_call__switches_to_a_named_worktree(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]
        self.context.cd_delivered = True

        self.run_command("feature")

        self.assertEqual([self.project_dir / "feature"], self.context.cd_requests)
        self.logger.info.assert_called_once()

    def test_call__accepts_a_trailing_slash(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]

        self.run_command("feature/")

        self.assertEqual([self.project_dir / "feature"], self.context.cd_requests)

    def test_call__switches_to_a_nested_worktree(self):
        self.git.list_worktrees.return_value = [
            self.add_worktree("subpath/feature", "refs/heads/subpath/feature")
        ]

        self.run_command("subpath/feature")

        self.assertEqual([self.project_dir / "subpath" / "feature"], self.context.cd_requests)

    def test_call__switches_to_a_checked_out_branch(self):
        self.git.list_worktrees.return_value = [
            self.add_worktree("custom-dir", "refs/heads/feature/my-feature")
        ]

        self.run_command("feature/my-feature")

        self.assertEqual([self.project_dir / "custom-dir"], self.context.cd_requests)

    def test_call__resolves_literal_names_without_rewriting(self):
        self.git.list_worktrees.return_value = [
            self.add_worktree("125", "refs/heads/one"),
            self.add_worktree("1.10", "refs/heads/two"),
            self.add_worktree("True", "refs/heads/three"),
        ]

        for name in ("125", "1.10", "True"):
            with self.subTest(name=name):
                self.context.cd_requests.clear()

                self.run_command(name)

                self.assertEqual([self.project_dir / name], self.context.cd_requests)

    def test_call__rejects_an_unknown_target(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]

        with self.assertRaisesRegex(
            WorktreeNotFoundError, "No worktree or checked-out branch named 'nope'"
        ):
            self.command("nope")

    def test_call__rejects_a_previous_worktree_when_none_is_recorded(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]

        with self.assertRaisesRegex(WorktreeNotFoundError, "No previous worktree recorded"):
            self.command("-")

    def test_call__rejects_a_stale_previous_worktree(self):
        self.config.last_worktree = "gone"
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]

        with self.assertRaisesRegex(
            WorktreeNotFoundError, "Previous worktree 'gone' is not a worktree in this project"
        ):
            self.command("-")

    def test_call__switches_to_the_previous_worktree(self):
        self.config.last_worktree = "feature"
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]

        self.run_command("-")

        self.assertEqual([self.project_dir / "feature"], self.context.cd_requests)

    def test_call__switches_to_the_default_branch_worktree(self):
        self.git.list_worktrees.return_value = [self.add_worktree("main-wt", "refs/heads/main")]

        self.run_command("^")

        self.assertEqual([self.project_dir / "main-wt"], self.context.cd_requests)

    def test_call__falls_back_to_the_project_root_for_the_default_branch(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]

        self.run_command("^")

        self.assertEqual([self.project_dir], self.context.cd_requests)

    def test_call__finds_the_default_branch_outside_the_project(self):
        outside = self.tmp_path / "elsewhere"
        outside.mkdir()
        self.git.list_worktrees.return_value = [
            {"path": str(outside), "branch": "refs/heads/main", "is_bare": False}
        ]

        self.run_command("^")

        self.assertEqual([outside], self.context.cd_requests)

    def test_call__switches_to_the_current_worktree_root(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]
        subdir = self.project_dir / "feature" / "src"
        subdir.mkdir()
        self.context.cwd = subdir

        for target in ("@", "."):
            with self.subTest(target=target):
                self.context.cd_requests.clear()

                self.run_command(target)

                self.assertEqual([self.project_dir / "feature"], self.context.cd_requests)

    def test_call__switches_to_the_project_root_from_the_root(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]
        self.context.cwd = self.project_dir

        self.run_command("@")

        self.assertEqual([self.project_dir], self.context.cd_requests)

    def test_call__records_the_worktree_being_left(self):
        self.git.list_worktrees.return_value = [
            self.add_worktree("feature", "refs/heads/feature"),
            self.add_worktree("other", "refs/heads/other"),
        ]
        self.context.cwd = self.project_dir / "feature"
        self.context.cd_delivered = True

        self.run_command("other")

        self.assertEqual([{"last_worktree": "feature"}], self.config.updates)
        self.assertEqual([self.config], self.context.saved_configs)

    def test_call__does_not_record_a_switch_to_the_current_worktree(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]
        self.context.cwd = self.project_dir / "feature"

        self.run_command("@")

        self.assertEqual([], self.config.updates)
        self.assertEqual([], self.context.saved_configs)

    def test_call__does_not_record_a_switch_from_the_project_root(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]
        self.context.cwd = self.project_dir

        self.run_command("feature")

        self.assertEqual([], self.config.updates)

    def test_call__treats_a_submodule_as_the_enclosing_worktree(self):
        """A nested repository must never reach ``last_worktree``."""
        self.git.list_worktrees.return_value = [
            self.add_worktree("feature", "refs/heads/feature"),
            self.add_worktree("other", "refs/heads/other"),
        ]
        submodule = self.project_dir / "feature" / "modules" / "lib"
        submodule.mkdir(parents=True)
        (submodule / ".git").write_text("gitdir: ../../../.bare/worktrees/feature/modules/lib\n")
        self.context.cwd = submodule

        self.run_command("other")

        self.assertEqual([{"last_worktree": "feature"}], self.config.updates)

    def test_call__prints_the_path_and_the_setup_hint_when_the_directive_is_unset(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]
        self.context.cd_delivered = False
        self.context.cd_enabled = False

        stdout, stderr = self.run_command("feature")

        self.assertEqual(f"{self.project_dir / 'feature'}\n", stdout)
        self.assertIn("treefort install --shell", stderr)
        self.logger.info.assert_not_called()

    def test_call__omits_the_setup_hint_when_the_directive_is_enabled(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]
        self.context.cd_delivered = False
        self.context.cd_enabled = True

        stdout, stderr = self.run_command("feature")

        self.assertEqual(f"{self.project_dir / 'feature'}\n", stdout)
        self.assertEqual("", stderr)
        self.logger.info.assert_not_called()

    def test_call__reports_the_switch_only_when_the_directive_is_delivered(self):
        self.git.list_worktrees.return_value = [self.add_worktree("feature", "refs/heads/feature")]
        self.context.cd_delivered = True

        stdout, stderr = self.run_command("feature")

        self.assertEqual("", stdout)
        self.assertEqual("", stderr)
        message = self.logger.info.call_args.args[0]
        self.assertIn("Switched to", message)
        self.assertIn(str(self.project_dir / "feature"), message)

    def test_call__excludes_the_bare_entry_and_the_project_root(self):
        self.git.list_worktrees.return_value = [
            {"path": str(self.project_dir / ".bare"), "branch": None, "is_bare": True},
            {"path": str(self.project_dir), "branch": "refs/heads/main", "is_bare": False},
        ]

        with self.assertRaises(WorktreeNotFoundError):
            self.command("project")

    def test_call__excludes_a_worktree_whose_directory_is_gone(self):
        self.git.list_worktrees.return_value = [
            {"path": str(self.project_dir / "gone"), "branch": "refs/heads/gone", "is_bare": False}
        ]

        with self.assertRaises(WorktreeNotFoundError):
            self.command("gone")

    def test_call__rejects_a_target_outside_the_project(self):
        outside = self.tmp_path / "outside"
        outside.mkdir()
        self.git.list_worktrees.return_value = [
            {"path": str(outside), "branch": "refs/heads/out", "is_bare": False}
        ]

        with self.assertRaises(WorktreeNotFoundError):
            self.command("../outside")
