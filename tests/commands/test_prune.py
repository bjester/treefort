import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from treefort.commands.prune import PruneCandidate, PruneCommand
from treefort.errors import CommandError
from treefort.hooks import Hook


class StubContext:
    def __init__(self, project_dir, config):
        self.project_dir = project_dir
        self._config = config
        self.assert_called = False
        self.used = None

    def assert_within_project(self):
        self.assert_called = True

    def get_config(self):
        return self._config

    @contextmanager
    def use(self, cwd, end_at=None):
        self.used = (cwd, end_at)
        yield


class PruneCommandTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.project_dir = self.tmp_path / "project"
        self.project_dir.mkdir()

        self.config = SimpleNamespace(owner="octo", name="repo")
        self.context = StubContext(self.project_dir, self.config)

        self.hooks = SimpleNamespace(fire=Mock())
        self.git = SimpleNamespace(
            list_worktrees=Mock(),
            get_branch_head=Mock(),
            remove_worktree=Mock(),
            delete_branch_if_head=Mock(),
        )
        self.gh = SimpleNamespace(merged_pr_by_head=Mock())
        self.logger = Mock()

        self.runtime = SimpleNamespace(
            context=self.context,
            hooks=self.hooks,
            git=self.git,
            gh=self.gh,
            logger=self.logger,
        )
        self.command = PruneCommand(self.runtime)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_call__no_worktrees_to_prune(self):
        self.git.list_worktrees.return_value = [
            {"path": str(self.project_dir), "branch": "refs/heads/main", "is_bare": False},
            {"path": "/bare/repo.git", "branch": None, "is_bare": True},
        ]

        self.command()

        self.assertTrue(self.context.assert_called)
        self.logger.info.assert_any_call("No merged PR worktrees found to prune.")

    def _one_eligible_worktree(self):
        worktree_path = self.project_dir / "feature-branch"
        worktree_path.mkdir()
        self.git.list_worktrees.return_value = [
            {"path": str(worktree_path), "branch": "refs/heads/feature-branch", "is_bare": False},
        ]
        self.gh.merged_pr_by_head.return_value = []

    def _assert_repo_arg_omitted(self):
        self.gh.merged_pr_by_head.assert_called_once_with("feature-branch", owner_repo=None)
        self.assertNotIn("None/None", str(self.gh.merged_pr_by_head.call_args_list))

    def test_call__uses_configured_owner_repo(self):
        self._one_eligible_worktree()

        self.command()

        self.gh.merged_pr_by_head.assert_called_once_with("feature-branch", owner_repo="octo/repo")

    def test_call__omits_repo_when_owner_missing(self):
        self.config.owner = None
        self._one_eligible_worktree()

        self.command()

        self._assert_repo_arg_omitted()

    def test_call__omits_repo_when_name_missing(self):
        self.config.name = None
        self._one_eligible_worktree()

        self.command()

        self._assert_repo_arg_omitted()

    def test_call__prunes_merged_worktree(self):
        wt_path = str(self.project_dir / "feature-branch")
        (self.project_dir / "feature-branch").mkdir()

        self.git.list_worktrees.return_value = [
            {"path": wt_path, "branch": "refs/heads/feature-branch", "is_bare": False},
        ]
        self.git.get_branch_head.return_value = "abc123def456"
        self.gh.merged_pr_by_head.return_value = [
            {
                "number": 123,
                "title": "Add awesome feature",
                "url": "https://github.com/octo/repo/pull/123",
                "headRefOid": "abc123def456",
            }
        ]

        with patch("builtins.input", return_value="y"):
            self.command()

        self.assertEqual(self.context.used, (self.project_dir, self.project_dir))
        self.git.remove_worktree.assert_called_once_with("feature-branch", force=False)
        self.git.delete_branch_if_head.assert_called_once_with("feature-branch", "abc123def456")
        self.hooks.fire.assert_any_call(
            Hook.pre_remove, "feature-branch", "feature-branch", bypass_allowlist=False
        )
        self.hooks.fire.assert_any_call(
            Hook.post_remove, "feature-branch", "feature-branch", bypass_allowlist=False
        )
        self.logger.info.assert_any_call("Successfully pruned 'feature-branch'")

    def test_call__prunes_nested_worktree(self):
        nested_dir = self.project_dir / "subpath" / "feature"
        nested_dir.mkdir(parents=True)

        self.git.list_worktrees.return_value = [
            {"path": str(nested_dir), "branch": "refs/heads/subpath/feature", "is_bare": False},
        ]
        self.git.get_branch_head.return_value = "abc123def456"
        self.gh.merged_pr_by_head.return_value = [
            {
                "number": 456,
                "title": "Nested feature",
                "url": "https://github.com/octo/repo/pull/456",
                "headRefOid": "abc123def456",
            }
        ]

        self.command(yes=True)

        self.git.remove_worktree.assert_called_once_with("subpath/feature", force=False)
        self.git.delete_branch_if_head.assert_called_once_with("subpath/feature", "abc123def456")
        self.hooks.fire.assert_any_call(
            Hook.pre_remove, "subpath/feature", "subpath-feature", bypass_allowlist=True
        )
        self.hooks.fire.assert_any_call(
            Hook.post_remove, "subpath/feature", "subpath-feature", bypass_allowlist=True
        )

    def test_call__skips_reused_branch(self):
        wt_path = str(self.project_dir / "reused-branch")
        (self.project_dir / "reused-branch").mkdir()

        self.git.list_worktrees.return_value = [
            {"path": wt_path, "branch": "refs/heads/reused-branch", "is_bare": False},
        ]
        self.git.get_branch_head.return_value = "newcommit1"
        self.gh.merged_pr_by_head.return_value = [
            {
                "number": 100,
                "title": "Old PR",
                "url": "https://github.com/octo/repo/pull/100",
                "headRefOid": "oldcommit1",
            }
        ]

        self.command()

        self.git.remove_worktree.assert_not_called()
        self.logger.warning.assert_called()
        warning_msg = str(self.logger.warning.call_args[0][0])
        self.assertIn("does not match any merged PR head", warning_msg)

    def test_call__skips_on_get_branch_head_error(self):
        wt_path = str(self.project_dir / "error-branch")
        (self.project_dir / "error-branch").mkdir()

        self.git.list_worktrees.return_value = [
            {"path": wt_path, "branch": "refs/heads/error-branch", "is_bare": False},
        ]
        self.git.get_branch_head.side_effect = CommandError("git failed")
        self.gh.merged_pr_by_head.return_value = [{"number": 1}]

        self.command()

        self.git.remove_worktree.assert_not_called()
        self.logger.warning.assert_called()

    def test_call__yes_true_skips_prompt(self):
        wt_path = str(self.project_dir / "auto-branch")
        (self.project_dir / "auto-branch").mkdir()

        self.git.list_worktrees.return_value = [
            {"path": wt_path, "branch": "refs/heads/auto-branch", "is_bare": False},
        ]
        self.git.get_branch_head.return_value = "match123"
        self.gh.merged_pr_by_head.return_value = [
            {"number": 1, "title": "Auto", "url": "url", "headRefOid": "match123"}
        ]

        self.command(yes=True)

        self.git.remove_worktree.assert_called_once()
        self.hooks.fire.assert_any_call(
            Hook.pre_remove, "auto-branch", "auto-branch", bypass_allowlist=True
        )

    def test_call__force_true_passed_to_git(self):
        wt_path = str(self.project_dir / "force-branch")
        (self.project_dir / "force-branch").mkdir()

        self.git.list_worktrees.return_value = [
            {"path": wt_path, "branch": "refs/heads/force-branch", "is_bare": False},
        ]
        self.git.get_branch_head.return_value = "match123"
        self.gh.merged_pr_by_head.return_value = [
            {"number": 1, "title": "Force", "url": "url", "headRefOid": "match123"}
        ]

        with patch("builtins.input", return_value="y"):
            self.command(force=True)

        self.git.remove_worktree.assert_called_once_with("force-branch", force=True)
        self.git.delete_branch_if_head.assert_called_once_with("force-branch", "match123")

    def test_call__handles_prune_failure_gracefully(self):
        wt_path = str(self.project_dir / "fail-branch")
        (self.project_dir / "fail-branch").mkdir()
        wt_path2 = str(self.project_dir / "success-branch")
        (self.project_dir / "success-branch").mkdir()

        self.git.list_worktrees.return_value = [
            {"path": wt_path, "branch": "refs/heads/fail-branch", "is_bare": False},
            {"path": wt_path2, "branch": "refs/heads/success-branch", "is_bare": False},
        ]
        self.git.get_branch_head.return_value = "match123"
        self.gh.merged_pr_by_head.return_value = [
            {"number": 1, "title": "Test", "url": "url", "headRefOid": "match123"}
        ]
        self.git.remove_worktree.side_effect = [CommandError("locked"), None]

        with patch("builtins.input", return_value="y"):
            self.command()

        self.logger.error.assert_called()
        self.logger.info.assert_any_call("Successfully pruned 'success-branch'")
        self.logger.info.assert_any_call("Pruning complete: 1 pruned, 1 failed.")

    def test_do_prune__returns_false_when_branch_deletion_fails(self):
        self.git.delete_branch_if_head.side_effect = CommandError("branch deletion failed")

        result = self.command._do_prune(
            PruneCandidate("fail-branch", "fail-branch", "verified-head"),
            force=False,
            yes=True,
        )

        self.assertFalse(result)
        self.hooks.fire.assert_any_call(
            Hook.pre_remove, "fail-branch", "fail-branch", bypass_allowlist=True
        )
        self.assertNotIn(
            Hook.post_remove, [call.args[0] for call in self.hooks.fire.call_args_list]
        )
        self.logger.error.assert_called_once_with(
            "Failed to prune 'fail-branch': branch deletion failed"
        )
        self.assertNotIn(
            "Successfully pruned 'fail-branch'",
            [call.args[0] for call in self.logger.info.call_args_list],
        )

    def test_call__counts_branch_deletion_failure(self):
        wt_path = str(self.project_dir / "fail-branch")
        (self.project_dir / "fail-branch").mkdir()

        self.git.list_worktrees.return_value = [
            {"path": wt_path, "branch": "refs/heads/fail-branch", "is_bare": False},
        ]
        self.git.get_branch_head.return_value = "match123"
        self.gh.merged_pr_by_head.return_value = [
            {"number": 1, "title": "Fail", "url": "url", "headRefOid": "match123"}
        ]
        self.git.delete_branch_if_head.side_effect = CommandError("branch deletion failed")

        self.command(yes=True)

        self.logger.info.assert_any_call("Pruning complete: 0 pruned, 1 failed.")
        self.assertNotIn(
            "Successfully pruned 'fail-branch'",
            [call.args[0] for call in self.logger.info.call_args_list],
        )
