import io
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch

from treefort.commands.switch import SwitchCommand
from treefort.config import RepositoryConfig, TomlConfigIO
from treefort.context import Context
from treefort.errors import ProjectNotFoundError, WorktreeNotFoundError

BRANCH_REF_PREFIX = "refs/heads/"


class SwitchCommandTestCase(TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.addCleanup(os.chdir, self.original_cwd)
        # Resolve against symlinked temp roots (e.g. macOS /tmp) so path comparisons match
        # the resolved paths the command reports.
        self.tmp_path = Path(self.tmp_dir.name).resolve()
        self.project_dir = self.make_project()

        self.context = Context()
        self.context.cwd = self.project_dir
        self.git = Mock()
        self.logger = Mock()
        self.runtime = SimpleNamespace(context=self.context, git=self.git, logger=self.logger)
        self.command = SwitchCommand(self.runtime)

    def make_project(self) -> Path:
        """Create a project layout with a config file and two worktrees."""
        project_dir = self.tmp_path / "project"
        (project_dir / ".bare").mkdir(parents=True)
        (project_dir / ".git").write_text("gitdir: ./.bare\n", encoding="utf-8")
        config = RepositoryConfig(TomlConfigIO.from_path(project_dir / ".treefort", "config"))
        config.update(default_branch="main")
        config.save()
        for name, gitdir in (("feature-a", "../.bare"), ("subpath/feature", "../../.bare")):
            worktree_dir = project_dir / name
            worktree_dir.mkdir(parents=True)
            (worktree_dir / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
        return project_dir

    def enter_worktree(self, name: str):
        self.context.cwd = self.project_dir / name

    def load_config(self) -> RepositoryConfig:
        config = RepositoryConfig(TomlConfigIO.from_path(self.project_dir / ".treefort", "config"))
        config.load()
        return config

    def record(self, name: str | None, branch: str | None) -> dict:
        path = self.project_dir if name is None else self.project_dir / name
        return {"path": str(path), "branch": branch, "is_bare": False}

    def bare_record(self) -> dict:
        return {"path": str(self.project_dir / ".bare"), "branch": None, "is_bare": True}

    def worktree_listing(self) -> list[dict]:
        return [
            self.bare_record(),
            self.record(None, f"{BRANCH_REF_PREFIX}main"),
            self.record("feature-a", f"{BRANCH_REF_PREFIX}feature-a"),
            self.record("subpath/feature", f"{BRANCH_REF_PREFIX}subfeat"),
        ]

    def switch(self, target: str):
        """Invoke the command with an active shell-integration directive file."""
        cd_file = self.tmp_path / "cd-file"
        with patch.dict(os.environ, {"TREEFORT_CD_FILE": str(cd_file)}):
            self.command(target)
        return cd_file

    def test_call__resolves_worktree_name(self):
        self.git.list_worktrees.return_value = self.worktree_listing()

        cd_file = self.switch("feature-a")

        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'feature-a'}\n")

    def test_call__resolves_nested_worktree_name(self):
        self.git.list_worktrees.return_value = self.worktree_listing()

        cd_file = self.switch("subpath/feature")

        self.assertEqual(
            cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'subpath' / 'feature'}\n"
        )

    def test_call__resolves_worktree_through_symlinked_project_dir(self):
        link = self.tmp_path / "link"
        link.symlink_to(self.project_dir)
        self.git.list_worktrees.return_value = self.worktree_listing()

        with patch.object(Context, "project_dir", new_callable=PropertyMock, return_value=link):
            cd_file = self.switch("feature-a")

        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'feature-a'}\n")

    def test_call__resolves_checked_out_branch_name(self):
        self.git.list_worktrees.return_value = self.worktree_listing()

        cd_file = self.switch("subfeat")

        self.assertEqual(
            cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'subpath' / 'feature'}\n"
        )

    def test_call__resolves_previous_worktree_shortcut(self):
        config = self.load_config()
        config.update(last_worktree="feature-a")
        self.context.set_config(config)
        self.git.list_worktrees.return_value = self.worktree_listing()

        cd_file = self.switch("-")

        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'feature-a'}\n")

    def test_call__raises_without_previous_worktree(self):
        with self.assertRaisesRegex(WorktreeNotFoundError, "No previous worktree recorded"):
            self.switch("-")

    def test_call__raises_when_previous_worktree_missing_on_disk(self):
        self.git.list_worktrees.return_value = self.worktree_listing()
        config = self.load_config()
        config.update(last_worktree="gone")
        self.context.set_config(config)

        with self.assertRaisesRegex(WorktreeNotFoundError, "No previous worktree recorded"):
            self.switch("-")

    def test_call__raises_when_previous_worktree_escapes_project_dir(self):
        for escaping_name in ("..", "../elsewhere", "/tmp"):
            config = self.load_config()
            config.update(last_worktree=escaping_name)
            self.context.set_config(config)

            with self.assertRaisesRegex(WorktreeNotFoundError, "No previous worktree recorded"):
                self.switch("-")

    def test_call__raises_when_previous_worktree_is_not_a_worktree(self):
        (self.project_dir / "nested-repo").mkdir()
        (self.project_dir / "nested-repo" / ".git").write_text("gitdir: ./\n", encoding="utf-8")
        self.git.list_worktrees.return_value = self.worktree_listing()
        for non_worktree in (".bare", ".treefort", "subpath", "nested-repo"):
            config = self.load_config()
            config.update(last_worktree=non_worktree)
            self.context.set_config(config)

            with self.assertRaisesRegex(WorktreeNotFoundError, "No previous worktree recorded"):
                self.switch("-")

    def test_call__canonicalizes_previous_worktree_name(self):
        self.git.list_worktrees.return_value = self.worktree_listing()
        config = self.load_config()
        config.update(last_worktree="./feature-a")
        self.context.set_config(config)

        cd_file = self.switch("-")

        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'feature-a'}\n")

    def test_call__resolves_default_branch_shortcut(self):
        (self.project_dir / "main-checkout").mkdir()
        self.git.list_worktrees.return_value = [
            self.bare_record(),
            self.record("feature-a", f"{BRANCH_REF_PREFIX}feature-a"),
            self.record("main-checkout", f"{BRANCH_REF_PREFIX}main"),
        ]

        cd_file = self.switch("^")

        self.assertEqual(
            cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'main-checkout'}\n"
        )

    def test_call__default_branch_shortcut_falls_back_to_project_dir(self):
        self.git.list_worktrees.return_value = [
            self.bare_record(),
            self.record("feature-a", f"{BRANCH_REF_PREFIX}feature-a"),
        ]

        cd_file = self.switch("^")

        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir}\n")

    def test_call__resolves_current_worktree_shortcut(self):
        self.enter_worktree("feature-a")

        cd_file = self.switch("@")

        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'feature-a'}\n")
        self.git.list_worktrees.assert_not_called()

    def test_call__current_worktree_shortcut_resolves_to_project_dir_at_root(self):
        cd_file = self.switch("@")

        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir}\n")
        self.git.list_worktrees.assert_not_called()

    def test_call__dot_alias_resolves_current_worktree(self):
        self.enter_worktree("subpath/feature")

        cd_file = self.switch(".")

        self.assertEqual(
            cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'subpath' / 'feature'}\n"
        )

    def test_call__records_last_worktree_when_switching_away(self):
        self.enter_worktree("feature-a")
        self.git.list_worktrees.return_value = self.worktree_listing()

        self.switch("subpath/feature")

        self.assertEqual(self.load_config().last_worktree, "feature-a")

    def test_call__does_not_record_last_worktree_from_project_root(self):
        self.git.list_worktrees.return_value = self.worktree_listing()

        self.switch("feature-a")

        self.assertIsNone(self.load_config().last_worktree)

    def test_call__does_not_record_last_worktree_when_switching_to_self(self):
        self.enter_worktree("feature-a")

        cd_file = self.switch("@")

        self.assertIsNone(self.load_config().last_worktree)
        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'feature-a'}\n")

    def test_call__does_not_record_last_worktree_for_nested_repository(self):
        nested_repo = self.project_dir / "feature-a" / "vendor" / "dep"
        nested_repo.mkdir(parents=True)
        (nested_repo / ".git").write_text("gitdir: ../.bare\n", encoding="utf-8")
        self.context.cwd = nested_repo
        self.git.list_worktrees.return_value = self.worktree_listing()

        self.switch("subpath/feature")

        self.assertIsNone(self.load_config().last_worktree)

    def test_call__records_last_worktree_for_default_branch_at_project_root(self):
        self.enter_worktree("feature-a")
        self.git.list_worktrees.return_value = self.worktree_listing()

        cd_file = self.switch("main")

        self.assertEqual(self.load_config().last_worktree, "feature-a")
        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir}\n")

    def test_call__toggles_between_worktrees_with_previous_shortcut(self):
        self.enter_worktree("feature-a")
        self.git.list_worktrees.return_value = self.worktree_listing()
        self.switch("subpath/feature")
        self.assertEqual(self.load_config().last_worktree, "feature-a")

        self.enter_worktree("subpath/feature")

        cd_file = self.switch("-")

        self.assertEqual(self.load_config().last_worktree, "subpath/feature")
        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{self.project_dir / 'feature-a'}\n")

    def test_call__raises_for_unknown_target(self):
        self.git.list_worktrees.return_value = self.worktree_listing()

        with self.assertRaisesRegex(WorktreeNotFoundError, "No worktree or branch named missing"):
            self.switch("missing")

    def test_call__raises_outside_project(self):
        self.context.cwd = self.tmp_path / "elsewhere"
        (self.tmp_path / "elsewhere").mkdir()

        with self.assertRaises(ProjectNotFoundError):
            self.command("feature-a")

    def test_call__prints_target_path_when_shell_integration_inactive(self):
        self.git.list_worktrees.return_value = self.worktree_listing()
        stdout, stderr = io.StringIO(), io.StringIO()

        with patch.dict(os.environ):
            os.environ.pop("TREEFORT_CD_FILE", None)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.command("feature-a")

        self.assertEqual(stdout.getvalue(), f"{self.project_dir / 'feature-a'}\n")
        self.assertIn("shell-init", stderr.getvalue())

    def test_call__logs_success_when_shell_integration_active(self):
        self.git.list_worktrees.return_value = self.worktree_listing()

        with patch("treefort.ui.is_color_enabled", return_value=False):
            self.switch("feature-a")

        self.logger.info.assert_called_once_with(f"✓ Switched to {self.project_dir / 'feature-a'}")

    def test_call__skips_deleted_worktree_in_listing(self):
        self.git.list_worktrees.return_value = [
            self.bare_record(),
            self.record("deleted", f"{BRANCH_REF_PREFIX}deleted"),
        ]

        with self.assertRaisesRegex(WorktreeNotFoundError, "No worktree or branch named deleted"):
            self.switch("deleted")
