import pathlib
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, Mock

from treefort.commands.init import InitCommand, RepositoryTarget, normalize
from treefort.errors import RepositoryPathError
from treefort.hooks import Hook


class StubContext:
    def __init__(self, cwd, config_dir):
        self.cwd = cwd
        self.config_dir = config_dir
        self.reset_called = False
        self.saved_configs = []

    @property
    def project_dir(self) -> pathlib.Path:
        return self.cwd

    @contextmanager
    def use(self, cwd):
        old_cwd = self.cwd
        self.cwd = cwd
        try:
            yield
        finally:
            self.cwd = old_cwd

    def reset_properties(self):
        self.reset_called = True

    def get_config(self):
        return None

    def get_global_config(self):
        return None

    def set_config(self, config):
        self.saved_configs.append(config)


class NormalizeTestCase(TestCase):
    def test_owner_repo(self):
        self.assertEqual(normalize("octo/repo"), "https://github.com/octo/repo.git")

    def test_no_dot_git(self):
        self.assertEqual(
            normalize("https://github.com/octo/repo"),
            "https://github.com/octo/repo.git",
        )

    def test_ssh(self):
        self.assertEqual(
            normalize("git@github.com:octo/repo.git"),
            "git@github.com:octo/repo.git",
        )

    def test_trailing_slash(self):
        self.assertEqual(
            normalize("https://github.com/octo/repo/"),
            "https://github.com/octo/repo.git",
        )
        self.assertEqual(
            normalize("octo/repo/"),
            "https://github.com/octo/repo.git",
        )

    def test_invalid_repo(self):
        with self.assertRaisesRegex(RepositoryPathError, "Invalid repository path format"):
            normalize("not a repo")


class RepositoryTargetTestCase(TestCase):
    def test_validate(self):
        target = RepositoryTarget("https://github.com/octo/repo.git")
        target.validate()

    def test_validate__invalid_path(self):
        target = RepositoryTarget("https://github.com/octo/repo/pull/123.git")
        with self.assertRaisesRegex(
            RepositoryPathError, "^Invalid repository path: octo/repo/pull/123$"
        ):
            target.validate()

    def test_path__url(self):
        target = RepositoryTarget("https://github.com/octo/repo.git")
        self.assertEqual(target.path, "octo/repo")

    def test_path__ssh(self):
        target = RepositoryTarget("git@github.com:octo/repo.git")
        self.assertEqual(target.path, "octo/repo")

    def test_path__owner_repo(self):
        target = RepositoryTarget("octo/repo")
        self.assertEqual(target.path, "octo/repo")

    def test_owner(self):
        target = RepositoryTarget("https://github.com/octo/repo.git")
        self.assertEqual(target.owner, "octo")

    def test_owner__ssh(self):
        target = RepositoryTarget("git@github.com:octo/repo.git")
        self.assertEqual(target.owner, "octo")
        self.assertEqual(target.name, "repo")

    def test_repo(self):
        target = RepositoryTarget("https://github.com/octo/repo.git")
        self.assertEqual(target.name, "repo")

    def test_destination_dir__default(self):
        target = RepositoryTarget("octo/repo")
        self.assertEqual(target.destination_dir, "repo")

    def test_destination_dir__custom(self):
        target = RepositoryTarget("octo/repo", destination_dir="custom-dir")
        self.assertEqual(target.destination_dir, "custom-dir")


class InitCommandTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self.tmp_dir.name)
        self.project_dir = pathlib.Path("project")
        self.config_dir = self.tmp_path / ".treefort"
        self.context = StubContext(self.tmp_path, self.config_dir)

        self.hooks = SimpleNamespace(fire=Mock(), add=MagicMock())
        self.templates = SimpleNamespace(add=MagicMock())
        self.git = SimpleNamespace(
            clone=Mock(),
            config=Mock(),
            fetch=Mock(),
            ls_tree=Mock(return_value=iter([])),
            cat_file=Mock(return_value=iter([])),
        )
        self.gh = SimpleNamespace(
            repo_status=Mock(
                return_value={
                    "defaultBranchRef": {"name": "main"},
                    "owner": {"login": "octo"},
                    "name": "repo",
                    "url": "https://github.com/octo/repo",
                    "isPrivate": False,
                }
            )
        )
        self.runtime = SimpleNamespace(
            logger=Mock(),
            context=self.context,
            hooks=self.hooks,
            templates=self.templates,
            git=self.git,
            gh=self.gh,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_call__basic(self):
        command = InitCommand(self.runtime)
        command("octo/repo", str(self.project_dir))

        self.git.clone.assert_called_once_with("https://github.com/octo/repo.git", ".bare")
        self.git.config.assert_called_once_with(
            "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"
        )
        self.git.fetch.assert_called_once_with()

        gitdir_file = self.tmp_path / self.project_dir / ".git"
        self.assertEqual(gitdir_file.read_text(), "gitdir: ./.bare")
        self.assertTrue(self.context.reset_called)
        self.assertTrue(len(self.context.saved_configs) == 1)
        config = self.context.saved_configs[0]
        self.assertEqual(config._config_io._file, self.config_dir / "config.toml")
        self.assertEqual(config.default_branch, "main")
        self.assertEqual(config.owner, "octo")
        self.assertEqual(config.name, "repo")

        self.hooks.fire.assert_any_call(
            Hook.pre_init,
            "https://github.com/octo/repo.git",
            self.tmp_path / self.project_dir,
            skip_project=True,
            bypass_allowlist=False,
        )
        self.hooks.fire.assert_any_call(
            Hook.post_init,
            "https://github.com/octo/repo.git",
            self.tmp_path / self.project_dir,
            bypass_allowlist=False,
        )

    def test_call__installs_hooks(self):

        def ls_tree_side_effect(branch, path):
            if path.endswith("post_checkout"):
                return iter(["100755 blob ...    post_checkout"])
            return iter([])

        self.git.ls_tree.side_effect = ls_tree_side_effect
        self.git.cat_file.return_value = iter(["echo 'hello'"])

        mock_f = self.runtime.hooks.add.return_value.__enter__.return_value

        command = InitCommand(self.runtime)
        command("octo/repo", str(self.project_dir))

        mock_f.write.assert_called_once_with("echo 'hello'\n")

    def test_call__installs_templates(self):
        self.git.ls_tree.return_value = iter(["100755 blob ...\t.treefort/templates/example.txt"])
        self.git.cat_file.return_value = iter(["hello"])
        mock_f = self.runtime.templates.add.return_value.__enter__.return_value

        command = InitCommand(self.runtime)
        command("octo/repo", str(self.project_dir))

        self.runtime.templates.add.assert_called_once_with(".treefort/templates/example.txt")
        mock_f.write.assert_called_once_with("hello\n")

    def test_add_templates__skips_existing(self):
        from treefort.templates import TemplateExists

        config = SimpleNamespace(default_branch="main")
        self.git.ls_tree.return_value = iter(["100755 blob ...\t.treefort/templates/example.txt"])
        self.runtime.templates.add.side_effect = TemplateExists(
            "Template .treefort/templates/example.txt already exists."
        )

        command = InitCommand(self.runtime)
        command._add_templates(config)

        self.runtime.templates.add.assert_called_once_with(".treefort/templates/example.txt")
        self.git.cat_file.assert_not_called()

    def test_call__yes_true_sets_bypass_allowlist_true(self):
        command = InitCommand(self.runtime)
        command("octo/repo", str(self.project_dir), yes=True)

        self.hooks.fire.assert_any_call(
            Hook.pre_init,
            "https://github.com/octo/repo.git",
            self.tmp_path / self.project_dir,
            skip_project=True,
            bypass_allowlist=True,
        )
        self.hooks.fire.assert_any_call(
            Hook.post_init,
            "https://github.com/octo/repo.git",
            self.tmp_path / self.project_dir,
            bypass_allowlist=True,
        )
