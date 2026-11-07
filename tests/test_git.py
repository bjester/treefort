import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from treefort.errors import CommandError, WorktreeNameError
from treefort.git import GitCLI, GitRemote, GitUri
from treefort.logger import setup_logger


class GitUriTestCase(TestCase):
    def test_path__https_with_git_ext(self):
        uri = GitUri("https://github.com/octo/repo.git")
        self.assertEqual(uri.path, "octo/repo")
        self.assertEqual(uri.owner, "octo")
        self.assertEqual(uri.name, "repo")
        self.assertTrue(uri.is_valid)

    def test_path__https_without_git_ext_and_trailing_slash(self):
        uri = GitUri("https://github.com/octo/repo/")
        self.assertEqual(uri.path, "octo/repo")
        self.assertEqual(uri.owner, "octo")
        self.assertEqual(uri.name, "repo")
        self.assertTrue(uri.is_valid)

    def test_path__https_uppercase_git_ext_and_trailing_slash(self):
        uri = GitUri("https://github.com/Octo/Repo.GIT/")
        self.assertEqual(uri.path, "Octo/Repo")
        self.assertEqual(uri.owner, "Octo")
        self.assertEqual(uri.name, "Repo")
        self.assertTrue(uri.is_valid)

    def test_path__ssh_scp_style(self):
        uri = GitUri("git@github.com:octo/repo.git")
        self.assertEqual(uri.path, "octo/repo")
        self.assertEqual(uri.owner, "octo")
        self.assertEqual(uri.name, "repo")
        self.assertTrue(uri.is_valid)

    def test_path__ssh_scheme_url(self):
        uri = GitUri("ssh://git@github.com/octo/repo.git")
        self.assertEqual(uri.path, "octo/repo")
        self.assertEqual(uri.owner, "octo")
        self.assertEqual(uri.name, "repo")
        self.assertTrue(uri.is_valid)

    def test_path__shorthand_owner_repo(self):
        uri = GitUri("octo/repo")
        self.assertEqual(uri.path, "octo/repo")
        self.assertEqual(uri.owner, "octo")
        self.assertEqual(uri.name, "repo")
        self.assertTrue(uri.is_valid)

    def test_is_valid__rejects_multi_segment_and_single_segment_paths(self):
        pr_uri = GitUri("https://github.com/octo/repo/pull/1234")
        self.assertEqual(pr_uri.owner, "octo")
        self.assertEqual(pr_uri.name, "repo")
        self.assertFalse(pr_uri.is_valid)

        single_uri = GitUri("uri")
        self.assertEqual(single_uri.owner, "uri")
        self.assertEqual(single_uri.name, "")
        self.assertFalse(single_uri.is_valid)

    def test_matches__case_insensitive_and_strict_boundaries(self):
        uri = GitUri("git@github.com:LearningEquality/Kolibri.git")
        self.assertTrue(uri.matches("learningequality", "kolibri"))
        self.assertTrue(uri.matches("LEARNINGEQUALITY", "KOLIBRI"))
        self.assertFalse(uri.matches("my-learningequality", "kolibri"))
        self.assertFalse(uri.matches("learningequality", "kolibri-ghsa-1234"))

        pr_uri = GitUri("https://github.com/octo/repo/pull/1234")
        self.assertFalse(pr_uri.matches("octo", "repo"))

    def test_eq_hash_and_repr(self):
        uri_a = GitUri("https://github.com/octo/repo.git")
        uri_b = GitUri("https://github.com/octo/repo.git")
        uri_c = GitUri("git@github.com:octo/repo.git")
        self.assertEqual(uri_a, uri_b)
        self.assertNotEqual(uri_a, uri_c)
        self.assertNotEqual(uri_a, "https://github.com/octo/repo.git")
        self.assertEqual(hash(uri_a), hash(uri_b))
        self.assertEqual(len({uri_a, uri_b, uri_c}), 2)
        self.assertEqual(repr(uri_a), "GitUri(uri='https://github.com/octo/repo.git')")


class GitRemoteTestCase(TestCase):
    def test_properties_and_matches(self):
        remote = GitRemote("origin", "git@github.com:Octo/Repo.git", "fetch")
        self.assertEqual(remote.git_uri, GitUri("git@github.com:Octo/Repo.git"))
        self.assertEqual(remote.owner, "Octo")
        self.assertEqual(remote.repo_name, "Repo")
        self.assertTrue(remote.matches("octo", "repo"))
        self.assertFalse(remote.matches("other", "repo"))

    def test_eq_hash_and_repr(self):
        remote_a = GitRemote("origin", "https://github.com/octo/repo.git", "fetch")
        remote_b = GitRemote("origin", "https://github.com/octo/repo.git", "fetch")
        remote_c = GitRemote("origin", "https://github.com/octo/repo.git", "push")
        self.assertEqual(remote_a, remote_b)
        self.assertNotEqual(remote_a, remote_c)
        self.assertNotEqual(remote_a, ("origin", "https://github.com/octo/repo.git", "fetch"))
        self.assertEqual(hash(remote_a), hash(remote_b))
        self.assertEqual(len({remote_a, remote_b, remote_c}), 2)
        self.assertEqual(
            repr(remote_a),
            "GitRemote(name='origin', uri='https://github.com/octo/repo.git', type='fetch')",
        )


class GitCLITestCase(TestCase):
    def setUp(self):
        self.context = SimpleNamespace(cwd=Path("/test/tmp"))
        self.logger = setup_logger("test")
        self.cli = GitCLI(self.context, self.logger)

        stream_exec_patcher = mock.patch("treefort.git.SubprocessOperator.stream_exec")
        self.mock_stream_exec = stream_exec_patcher.start()
        self.addCleanup(stream_exec_patcher.stop)
        self.mock_stream_exec.return_value = 0

        iter_output_patcher = mock.patch("treefort.git.SubprocessOperator.iter_output")
        self.mock_iter_output = iter_output_patcher.start()
        self.addCleanup(iter_output_patcher.stop)

    def test_stream_exec(self):
        self.cli.stream_exec(["foo", "bar"])
        self.mock_stream_exec.assert_called_once_with(["foo", "bar"], cwd=None)

    def test_stream_exec__cwd_passthrough(self):
        self.cli.stream_exec(["foo", "bar"], cwd="/tmp")
        self.mock_stream_exec.assert_called_once_with(["foo", "bar"], cwd="/tmp")

    def test_stream_exec__non_zero_exit(self):
        self.mock_stream_exec.return_value = 1
        with self.assertRaises(CommandError):
            self.cli.stream_exec(["foo", "bar"], cwd=None)

    def test_stream_exec__non_zero_exit_includes_last_output(self):
        self.mock_stream_exec.return_value = 128
        self.cli.last_output = ["fatal: a branch named 'foo' already exists"]

        with self.assertRaises(CommandError) as ctx:
            self.cli.stream_exec(["worktree", "add", "-b", "foo", "--", "foo", "main"])

        self.assertIn("Command failed, with exit status 128", str(ctx.exception))
        self.assertIn("fatal: a branch named 'foo' already exists", str(ctx.exception))

    def test_clone(self):
        self.cli.clone("src_uri", "dest_dir")
        self.mock_stream_exec.assert_called_once_with(
            ["clone", "--bare", "src_uri", "dest_dir"], cwd=None
        )

    def test_config(self):
        self.cli.config("user.name", "foo")
        self.mock_stream_exec.assert_called_once_with(["config", "user.name", "foo"], cwd=None)

    def test_ls_tree(self):
        self.mock_iter_output.return_value = ["line1", "line2"]
        lines = list(self.cli.ls_tree("main", "path/to/file"))
        self.mock_iter_output.assert_called_once_with(
            ["ls-tree", "-r", "main", "--", "path/to/file"],
        )
        self.assertEqual(lines, ["line1", "line2"])

    def test_cat_file(self):
        self.mock_iter_output.return_value = ["content"]
        lines = list(self.cli.cat_file("main", "file.txt"))
        self.mock_iter_output.assert_called_once_with(["cat-file", "-p", "main:file.txt"])
        self.assertEqual(lines, ["content"])

    def test_fetch(self):
        self.cli.fetch()
        self.mock_stream_exec.assert_called_with(["fetch", "origin"], cwd=None)

        self.cli.fetch(remote="upstream", refspec="master")
        self.mock_stream_exec.assert_called_with(["fetch", "upstream", "master"], cwd=None)

    def test_remote(self):
        self.mock_iter_output.return_value = [
            "origin\thttps://github.com/foo/bar (fetch)",
            "origin\thttps://github.com/foo/bar (push)",
        ]
        remotes = self.cli.remote()
        self.mock_iter_output.assert_called_once_with(["remote", "-v"])
        self.assertEqual(
            remotes,
            [
                GitRemote("origin", "https://github.com/foo/bar", "fetch"),
                GitRemote("origin", "https://github.com/foo/bar", "push"),
            ],
        )

    def test_add_worktree(self):
        self.cli.add_worktree("new-branch", "main")
        self.mock_stream_exec.assert_called_once_with(
            ["worktree", "add", "-b", "new-branch", "--", "new-branch", "main"], cwd=None
        )

    def test_open_worktree(self):
        self.cli.open_worktree("existing-branch")
        self.mock_stream_exec.assert_called_once_with(
            ["worktree", "add", "--", "existing-branch", "existing-branch"], cwd=None
        )

    def test_open_worktree__with_slash_in_name(self):
        self.cli.open_worktree("feature/existing-branch")
        self.mock_stream_exec.assert_called_once_with(
            [
                "worktree",
                "add",
                "--",
                "feature/existing-branch",
                "feature/existing-branch",
            ],
            cwd=None,
        )

    def test_open_worktree_validation(self):
        with self.assertRaises(WorktreeNameError):
            self.cli.open_worktree("../outside")
        with self.assertRaises(WorktreeNameError):
            self.cli.open_worktree("/absolute")

    def test_remove_worktree(self):
        self.cli.remove_worktree("old-tree")
        self.mock_stream_exec.assert_called_with(["worktree", "remove", "--", "old-tree"], cwd=None)

        self.cli.remove_worktree("old-tree", force=True)
        self.mock_stream_exec.assert_called_with(
            ["worktree", "remove", "--force", "--", "old-tree"], cwd=None
        )

    def test_list_worktrees(self):
        self.mock_iter_output.return_value = [
            "worktree /repo",
            "HEAD 0123456789abcdef0123456789abcdef01234567",
            "branch refs/heads/main",
            "",
            "worktree /repo/.git/worktrees/feature",
            "HEAD 89abcdef0123456789abcdef0123456789abcdef",
            "branch refs/heads/feature/existing-branch",
            "",
            "worktree /repo/.git/worktrees/detached",
            "HEAD fedcba9876543210fedcba9876543210fedcba98",
            "detached HEAD",
            "",
            "worktree /bare-clone",
            "HEAD 000102030405060708090a0b0c0d0e0f10111213",
            "bare",
            "",
        ]

        worktrees = self.cli.list_worktrees()

        self.mock_iter_output.assert_called_once_with(["worktree", "list", "--porcelain"])
        self.assertEqual(
            worktrees,
            [
                {"path": "/repo", "branch": "refs/heads/main", "is_bare": False},
                {
                    "path": "/repo/.git/worktrees/feature",
                    "branch": "refs/heads/feature/existing-branch",
                    "is_bare": False,
                },
                {"path": "/repo/.git/worktrees/detached", "branch": None, "is_bare": False},
                {"path": "/bare-clone", "branch": None, "is_bare": True},
            ],
        )

    def test_delete_branch(self):
        self.cli.delete_branch("feature-branch")
        self.mock_stream_exec.assert_called_once_with(
            ["branch", "-d", "--", "feature-branch"], cwd=None
        )

    def test_delete_branch__force(self):
        self.cli.delete_branch("feature-branch", force=True)
        self.mock_stream_exec.assert_called_once_with(
            ["branch", "-D", "--", "feature-branch"], cwd=None
        )

    def test_get_branch_head(self):
        run_patcher = mock.patch("treefort.git.SubprocessOperator.run")
        mock_run = run_patcher.start()
        self.addCleanup(run_patcher.stop)

        mock_process = mock.MagicMock()
        mock_process.stdout = "abc123def456\n"
        mock_run.return_value.__enter__.return_value = mock_process

        result = self.cli.get_branch_head("feature-branch")
        mock_run.assert_called_once_with(["rev-parse", "feature-branch"])
        self.assertEqual(result, "abc123def456")

    def test_get_branch_head__translates_called_process_error_with_last_output(self):
        run_patcher = mock.patch("treefort.git.SubprocessOperator.run")
        mock_run = run_patcher.start()
        self.addCleanup(run_patcher.stop)

        self.cli.last_output = ["fatal: ambiguous argument 'missing': unknown revision"]
        mock_run.return_value.__enter__.side_effect = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "rev-parse", "missing"],
        )

        with self.assertRaises(CommandError) as ctx:
            self.cli.get_branch_head("missing")

        self.assertIn(
            "Command failed, with exit status 128: git rev-parse missing", str(ctx.exception)
        )
        self.assertIn("fatal: ambiguous argument 'missing': unknown revision", str(ctx.exception))


class GitCLIDeleteBranchIfHeadTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.run_git("init", "--quiet")
        self.run_git("config", "user.name", "Treefort Test")
        self.run_git("config", "user.email", "treefort@example.invalid")
        self.run_git("commit", "--allow-empty", "--quiet", "-m", "initial")
        self.run_git("branch", "feature")

        context = SimpleNamespace(cwd=self.repo_dir)
        self.cli = GitCLI(context, setup_logger("test"))

    def tearDown(self):
        self.tmp_dir.cleanup()

    def run_git(self, *args: str) -> str:
        process = subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return process.stdout.strip()

    def git_succeeds(self, *args: str) -> bool:
        process = subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
        )
        return process.returncode == 0

    def test_delete_branch_if_head__retains_branch_when_head_changed(self):
        verified_head = self.run_git("rev-parse", "feature")
        self.run_git("commit", "--allow-empty", "--quiet", "-m", "advanced")
        advanced_head = self.run_git("rev-parse", "HEAD")
        self.run_git("branch", "--force", "feature", advanced_head)

        with self.assertRaises(CommandError):
            self.cli.delete_branch_if_head("feature", verified_head)

        self.assertEqual(self.run_git("rev-parse", "feature"), advanced_head)

    def test_delete_branch_if_head__removes_matching_branch_and_branch_config(self):
        expected_head = self.run_git("rev-parse", "feature")
        self.run_git("config", "branch.feature.remote", "origin")

        self.cli.delete_branch_if_head("feature", expected_head)

        self.assertFalse(self.git_succeeds("show-ref", "--verify", "refs/heads/feature"))
        self.assertFalse(self.git_succeeds("config", "--get", "branch.feature.remote"))

        self.run_git("branch", "without-config")
        expected_head = self.run_git("rev-parse", "without-config")

        self.cli.delete_branch_if_head("without-config", expected_head)

        self.assertFalse(self.git_succeeds("show-ref", "--verify", "refs/heads/without-config"))
