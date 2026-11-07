import os
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from treefort.context import Context
from treefort.errors import ProjectNotFoundError


class ContextTestCase(TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.context = Context()
        self.context.cwd = self.tmp_path

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.tmp_dir.cleanup()

    def make_project(self) -> Path:
        """Create a minimal project layout with a top-level and a nested worktree."""
        project_dir = self.tmp_path / "project"
        (project_dir / ".bare").mkdir(parents=True)
        (project_dir / ".git").write_text("gitdir: ./.bare\n", encoding="utf-8")
        for name, gitdir in (("feature-a", "../.bare"), ("subpath/feature", "../../.bare")):
            worktree_dir = project_dir / name
            worktree_dir.mkdir(parents=True)
            (worktree_dir / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
        return project_dir

    def test_assert_within_project__success(self):
        (self.tmp_path / ".bare").mkdir()

        self.context.assert_within_project()

    def test_assert_within_project__failure(self):
        with self.assertRaises(ProjectNotFoundError):
            self.context.assert_within_project()

    def test_project_dir__cached(self):
        (self.tmp_path / ".bare").mkdir()

        project_dir = self.context.project_dir
        self.assertEqual(project_dir, self.tmp_path)

        project_dir_again = self.context.project_dir
        self.assertIs(project_dir_again, project_dir)

    def test_reset_properties(self):
        (self.tmp_path / ".bare").mkdir()

        _ = self.context.project_dir
        self.context.reset_properties()

        self.assertIsNone(self.context._cached_project_dir)

    def test_request_cd__returns_false_when_shell_integration_inactive(self):
        with patch.dict(os.environ):
            os.environ.pop("TREEFORT_CD_FILE", None)

            self.assertFalse(self.context.request_cd(self.tmp_path))

    def test_request_cd__returns_false_when_cd_file_is_empty_string(self):
        with patch.dict(os.environ, {"TREEFORT_CD_FILE": ""}):
            self.assertFalse(self.context.request_cd(self.tmp_path))

    def test_request_cd__writes_resolved_target_to_cd_file(self):
        target_dir = self.tmp_path / "project" / "feature-a"
        cd_file = self.tmp_path / "cd-file"

        with patch.dict(os.environ, {"TREEFORT_CD_FILE": str(cd_file)}):
            result = self.context.request_cd(target_dir)

        self.assertTrue(result)
        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{target_dir.resolve()}\n")

    def test_request_cd__warns_and_returns_false_when_cd_file_unwritable(self):
        cd_file = self.tmp_path / "missing-dir" / "cd-file"

        with patch.dict(os.environ, {"TREEFORT_CD_FILE": str(cd_file)}):
            with self.assertLogs("treefort.context", level="WARNING") as logs:
                result = self.context.request_cd(self.tmp_path)

        self.assertFalse(result)
        self.assertFalse(cd_file.exists())
        self.assertTrue(any(str(cd_file) in message for message in logs.output))

    def test_current_worktree_name__none_at_project_root(self):
        project_dir = self.make_project()
        self.context.cwd = project_dir

        self.assertIsNone(self.context.current_worktree_name())

    def test_current_worktree_name__inside_top_level_worktree(self):
        project_dir = self.make_project()
        self.context.cwd = project_dir / "feature-a"

        self.assertEqual(self.context.current_worktree_name(), "feature-a")

    def test_current_worktree_name__inside_worktree_subdirectory(self):
        project_dir = self.make_project()
        worktree_subdir = project_dir / "feature-a" / "src"
        worktree_subdir.mkdir()
        self.context.cwd = worktree_subdir

        self.assertEqual(self.context.current_worktree_name(), "feature-a")

    def test_current_worktree_name__inside_nested_worktree(self):
        project_dir = self.make_project()
        self.context.cwd = project_dir / "subpath" / "feature"

        self.assertEqual(self.context.current_worktree_name(), "subpath/feature")

    def test_current_worktree_name__inside_bare_repository(self):
        project_dir = self.make_project()
        self.context.cwd = project_dir / ".bare"

        self.assertIsNone(self.context.current_worktree_name())

    def test_current_worktree_name__outside_project(self):
        outside_dir = self.tmp_path / "elsewhere"
        outside_dir.mkdir()
        self.context.cwd = outside_dir

        self.assertIsNone(self.context.current_worktree_name())

    def test_use__restores_previous_cwd(self):
        project_dir = self.make_project()
        old_cwd = project_dir / "feature-a"
        self.context.cwd = old_cwd
        os.chdir(old_cwd)

        with self.context.use(project_dir):
            self.assertEqual(self.context.cwd, project_dir)

        self.assertEqual(self.context.cwd, old_cwd)
        self.assertEqual(Path.cwd(), old_cwd)

    def test_use__stores_resolved_cwd_for_relative_paths(self):
        project_dir = self.make_project()
        os.chdir(self.tmp_path)

        with self.context.use("project"):
            self.assertEqual(self.context.cwd, project_dir.resolve())

    def test_use__falls_back_to_project_dir_when_old_cwd_deleted(self):
        project_dir = self.make_project()
        old_cwd = project_dir / "feature-a"
        self.context.cwd = old_cwd
        os.chdir(old_cwd)

        with self.context.use(project_dir):
            shutil.rmtree(old_cwd)

        self.assertEqual(self.context.cwd, project_dir)
        self.assertEqual(Path.cwd(), project_dir)

    def test_use__requests_cd_to_project_dir_when_old_cwd_deleted(self):
        project_dir = self.make_project()
        old_cwd = project_dir / "feature-a"
        cd_file = self.tmp_path / "cd-file"
        self.context.cwd = old_cwd
        os.chdir(old_cwd)

        with patch.dict(os.environ, {"TREEFORT_CD_FILE": str(cd_file)}):
            with self.assertNoLogs("treefort.context", level="WARNING"):
                with self.context.use(project_dir):
                    shutil.rmtree(old_cwd)

        self.assertEqual(cd_file.read_text(encoding="utf-8"), f"{project_dir.resolve()}\n")

    def test_use__warns_when_cd_directive_write_fails(self):
        project_dir = self.make_project()
        old_cwd = project_dir / "feature-a"
        cd_file = self.tmp_path / "missing-dir" / "cd-file"
        self.context.cwd = old_cwd
        os.chdir(old_cwd)

        with patch.dict(os.environ, {"TREEFORT_CD_FILE": str(cd_file)}):
            with self.assertLogs("treefort.context", level="WARNING") as logs:
                with self.context.use(project_dir):
                    shutil.rmtree(old_cwd)

        # The shell cannot be moved, so tell the user where to go instead of leaving it in a
        # deleted directory without a way out.
        self.assertTrue(any(f'cd "{project_dir.resolve()}"' in message for message in logs.output))

    def test_use__does_not_warn_about_cd_when_shell_integration_inactive(self):
        project_dir = self.make_project()
        old_cwd = project_dir / "feature-a"
        self.context.cwd = old_cwd
        os.chdir(old_cwd)

        with patch.dict(os.environ):
            os.environ.pop("TREEFORT_CD_FILE", None)
            with self.assertNoLogs("treefort.context", level="WARNING"):
                with self.context.use(project_dir):
                    shutil.rmtree(old_cwd)

    def test_shell_integration_active__reflects_cd_file_env_var(self):
        with patch.dict(os.environ, {"TREEFORT_CD_FILE": str(self.tmp_path / "cd-file")}):
            self.assertTrue(self.context.shell_integration_active)

        with patch.dict(os.environ, {"TREEFORT_CD_FILE": ""}):
            self.assertFalse(self.context.shell_integration_active)

        with patch.dict(os.environ):
            os.environ.pop("TREEFORT_CD_FILE", None)
            self.assertFalse(self.context.shell_integration_active)
