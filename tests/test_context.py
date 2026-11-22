import io
import os
import shlex
import shutil
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from treefort.context import CD_FILE_ENV_VAR, Context
from treefort.errors import ProjectNotFoundError


class ContextTestCase(TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.addCleanup(os.chdir, self.original_cwd)
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.tmp_path = Path(self.tmp_dir.name)
        self.context = Context()
        self.context.cwd = self.tmp_path

    def add_project(self) -> Path:
        """Create the ``.bare`` marker that makes ``tmp_path`` a project directory."""
        (self.tmp_path / ".bare").mkdir(exist_ok=True)
        return self.tmp_path

    def add_worktree(self, name: str, gitdir: str | None = None) -> Path:
        """Create a worktree whose ``.git`` file points at a gitdir target.

        Git names the worktree's admin directory after the path's basename, so a nested
        worktree such as ``subpath/feature`` is administered from ``worktrees/feature``.

        :param name: The project-relative worktree name
        :param gitdir: The recorded gitdir target, defaulting to the managed admin directory
        :return: The worktree directory
        """
        worktree = self.tmp_path / name
        worktree.mkdir(parents=True)
        admin_dir = self.tmp_path / ".bare" / "worktrees" / Path(name).name
        admin_dir.mkdir(parents=True, exist_ok=True)
        target = str(admin_dir) if gitdir is None else gitdir
        (worktree / ".git").write_text(f"gitdir: {target}\n", encoding="utf-8")
        return worktree

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

    def test_is_cd_enabled__false_when_the_variable_is_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(CD_FILE_ENV_VAR, None)

            self.assertFalse(self.context.is_cd_enabled())

    def test_is_cd_enabled__false_when_the_variable_is_empty(self):
        with patch.dict(os.environ, {CD_FILE_ENV_VAR: ""}):
            self.assertFalse(self.context.is_cd_enabled())

    def test_is_cd_enabled__true_when_the_variable_is_set(self):
        with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(self.tmp_path / "cd")}):
            self.assertTrue(self.context.is_cd_enabled())

    def test_request_cd__returns_false_when_the_variable_is_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(CD_FILE_ENV_VAR, None)

            self.assertFalse(self.context.request_cd(self.tmp_path))

    def test_request_cd__writes_the_resolved_target_path(self):
        cd_file = self.tmp_path / "cd"
        target = self.tmp_path / "target"
        target.mkdir()

        with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(cd_file)}):
            self.assertTrue(self.context.request_cd(target))

        self.assertEqual(f"{target.resolve()}\n", cd_file.read_text(encoding="utf-8"))

    def test_request_cd__resolves_a_relative_target_path(self):
        cd_file = self.tmp_path / "cd"
        target = self.tmp_path / "target"
        target.mkdir()

        with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(cd_file)}):
            os.chdir(self.tmp_path)
            self.context.request_cd(Path("target"))

        self.assertEqual(f"{target.resolve()}\n", cd_file.read_text(encoding="utf-8"))

    def test_request_cd__returns_false_and_warns_when_the_file_is_unwritable(self):
        target = self.tmp_path / "target"
        target.mkdir()
        unwritable = self.tmp_path / "missing" / "cd"

        with (
            patch.dict(os.environ, {CD_FILE_ENV_VAR: str(unwritable)}),
            patch("treefort.context.logging.getLogger") as get_logger_mock,
        ):
            delivered = self.context.request_cd(target)

        self.assertFalse(delivered)
        get_logger_mock.assert_called_once_with("treefort")
        warning = get_logger_mock.return_value.warning.call_args.args[0]
        self.assertIn(f"Could not write directory change to {unwritable}", warning)

    def test_current_worktree_name__returns_none_outside_a_project(self):
        self.assertIsNone(self.context.current_worktree_name())

    def test_current_worktree_name__returns_none_at_the_project_root(self):
        self.add_project()

        self.assertIsNone(self.context.current_worktree_name())

    def test_current_worktree_name__returns_none_inside_the_bare_directory(self):
        self.add_project()
        bare_subdir = self.tmp_path / ".bare" / "refs"
        bare_subdir.mkdir(parents=True)
        self.context.cwd = bare_subdir

        self.assertIsNone(self.context.current_worktree_name())

    def test_current_worktree_name__returns_the_worktree_name(self):
        self.add_project()
        worktree = self.add_worktree("feature")
        self.context.cwd = worktree

        self.assertEqual("feature", self.context.current_worktree_name())

    def test_current_worktree_name__returns_a_nested_worktree_name(self):
        self.add_project()
        worktree = self.add_worktree("subpath/feature")
        self.context.cwd = worktree

        self.assertEqual("subpath/feature", self.context.current_worktree_name())

    def test_current_worktree_name__returns_the_name_from_a_subdirectory(self):
        self.add_project()
        worktree = self.add_worktree("feature")
        subdir = worktree / "src" / "pkg"
        subdir.mkdir(parents=True)
        self.context.cwd = subdir

        self.assertEqual("feature", self.context.current_worktree_name())

    def test_current_worktree_name__ignores_a_dot_git_directory(self):
        self.add_project()
        clone = self.tmp_path / "clone"
        (clone / ".git").mkdir(parents=True)
        self.context.cwd = clone

        self.assertIsNone(self.context.current_worktree_name())

    def test_current_worktree_name__ignores_a_submodule(self):
        self.add_project()
        worktree = self.add_worktree("feature")
        submodule = worktree / "modules" / "lib"
        submodule.mkdir(parents=True)
        admin_dir = self.tmp_path / ".bare" / "worktrees" / "feature" / "modules" / "lib"
        admin_dir.mkdir(parents=True)
        (submodule / ".git").write_text(f"gitdir: {admin_dir}\n", encoding="utf-8")
        self.context.cwd = submodule

        self.assertEqual("feature", self.context.current_worktree_name())

    def test_current_worktree_name__ignores_a_foreign_worktree(self):
        self.add_project()
        foreign_admin = self.tmp_path / "elsewhere" / ".bare" / "worktrees" / "other"
        foreign_admin.mkdir(parents=True)
        foreign = self.tmp_path / "foreign"
        foreign.mkdir()
        (foreign / ".git").write_text(f"gitdir: {foreign_admin}\n", encoding="utf-8")
        self.context.cwd = foreign

        self.assertIsNone(self.context.current_worktree_name())

    def test_current_worktree_name__ignores_a_git_file_without_a_gitdir_line(self):
        self.add_project()
        stray = self.tmp_path / "stray"
        stray.mkdir()
        (stray / ".git").write_text("not a gitdir pointer\n", encoding="utf-8")
        self.context.cwd = stray

        self.assertIsNone(self.context.current_worktree_name())

    def test_current_worktree_name__ignores_a_git_file_that_cannot_be_read(self):
        self.add_project()
        worktree = self.add_worktree("feature")
        self.context.cwd = worktree

        with patch.object(Path, "read_text", side_effect=OSError("boom")):
            self.assertIsNone(self.context.current_worktree_name())

    def test_current_worktree_name__ignores_a_git_file_that_is_not_utf8(self):
        self.add_project()
        worktree = self.tmp_path / "broken"
        worktree.mkdir()
        (worktree / ".git").write_bytes(b"gitdir: \xff\xfe\n")
        self.context.cwd = worktree

        self.assertIsNone(self.context.current_worktree_name())

    def test_use__restores_the_original_cwd(self):
        original = self.tmp_path / "original"
        original.mkdir()
        target = self.tmp_path / "target"
        target.mkdir()
        self.context.cwd = original

        with self.context.use(target):
            self.assertEqual(target.resolve(), Path.cwd())

        self.assertEqual(original.resolve(), self.context.cwd)
        self.assertEqual(original.resolve(), Path.cwd())

    def test_use__recovers_in_end_at_when_the_original_cwd_is_deleted(self):
        project_dir = self.tmp_path / "project"
        project_dir.mkdir()
        worktree = self.tmp_path / "worktree"
        worktree.mkdir()
        self.context.cwd = worktree
        cd_file = self.tmp_path / "cd"

        with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(cd_file)}):
            with self.context.use(project_dir, end_at=project_dir):
                shutil.rmtree(worktree)

        self.assertEqual(f"{project_dir.resolve()}\n", cd_file.read_text(encoding="utf-8"))
        self.assertEqual(project_dir.resolve(), self.context.cwd)
        self.assertEqual(project_dir.resolve(), Path.cwd())

    def test_use__falls_back_to_the_entered_cwd_when_end_at_is_omitted(self):
        worktree = self.tmp_path / "worktree"
        worktree.mkdir()
        target = self.tmp_path / "target"
        target.mkdir()
        self.context.cwd = worktree

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(CD_FILE_ENV_VAR, None)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with self.context.use(target):
                    shutil.rmtree(worktree)

        self.assertEqual(target.resolve(), self.context.cwd)
        self.assertIn(f"cd -- {shlex.quote(str(target.resolve()))}", stderr.getvalue())

    def test_use__writes_a_shell_escaped_warning_without_home_substitution(self):
        home_dir = self.tmp_path / "home with space"
        home_dir.mkdir()
        fallback = home_dir / "project"
        fallback.mkdir()
        deleted = home_dir / "gone"
        deleted.mkdir()
        self.context.cwd = deleted

        with patch.dict(os.environ, {"HOME": str(home_dir)}, clear=False):
            os.environ.pop(CD_FILE_ENV_VAR, None)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with self.context.use(fallback, end_at=fallback):
                    shutil.rmtree(deleted)

        warning = stderr.getvalue()
        self.assertIn(f"cd -- {shlex.quote(str(fallback.resolve()))}", warning)
        self.assertIn("Working directory was removed", warning)
        self.assertNotIn("~/", warning)

    def test_use__writes_no_warning_when_the_directive_is_delivered(self):
        fallback = self.tmp_path / "project"
        fallback.mkdir()
        deleted = self.tmp_path / "gone"
        deleted.mkdir()
        self.context.cwd = deleted
        cd_file = self.tmp_path / "cd"

        with patch.dict(os.environ, {CD_FILE_ENV_VAR: str(cd_file)}):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with self.context.use(fallback, end_at=fallback):
                    shutil.rmtree(deleted)

        self.assertEqual("", stderr.getvalue())
        self.assertEqual(f"{fallback.resolve()}\n", cd_file.read_text(encoding="utf-8"))
