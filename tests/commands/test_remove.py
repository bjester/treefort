import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, call, patch

from treefort.commands.remove import RemoveCommand
from treefort.errors import CommandError, WorktreeNotFoundError
from treefort.hooks import Hook


class StubContext:
    def __init__(self, project_dir):
        self.project_dir = project_dir

    @contextmanager
    def use(self, cwd):
        yield


class RemoveCommandTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.project_dir = self.tmp_path / "project"
        self.project_dir.mkdir()
        self.context = StubContext(self.project_dir)
        self.hooks = Mock()
        self.git = Mock()
        self.logger = Mock()
        self.runtime = SimpleNamespace(
            context=self.context,
            hooks=self.hooks,
            git=self.git,
            logger=self.logger,
        )
        self.command = RemoveCommand(self.runtime)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def add_worktree(self, name):
        (self.project_dir / name).mkdir()

    def worktree_record(self, name, branch):
        return {"path": str(self.project_dir / name), "branch": branch, "is_bare": False}

    def assert_skips_branch_deletion_with_warning(self):
        self.git.delete_branch.assert_not_called()
        self.logger.warning.assert_called_once_with(
            "Skipping branch deletion for worktree 'detached': no associated local branch found."
        )

    def test_call__raises_when_missing(self):
        with self.assertRaisesRegex(WorktreeNotFoundError, "Worktree missing does not exist"):
            self.command("missing")

    def test_call__runs_hooks_and_git(self):
        self.add_worktree("feature")
        self.git.list_worktrees.return_value = [
            self.worktree_record("feature", "refs/heads/feature")
        ]

        self.command("feature", force=True, delete_branch=True)

        self.git.remove_worktree.assert_called_once_with("feature", force=True)
        self.git.delete_branch.assert_called_once_with("feature", force=True)
        self.hooks.fire.assert_any_call(
            Hook.pre_remove, "feature", "feature", bypass_allowlist=False
        )
        self.hooks.fire.assert_any_call(
            Hook.post_remove, "feature", "feature", bypass_allowlist=False
        )

    def test_call__deletes_branch_associated_with_mismatched_worktree(self):
        parent = Mock()
        runtime = SimpleNamespace(
            context=self.context,
            hooks=parent.hooks,
            git=parent.git,
            logger=parent.logger,
        )
        self.add_worktree("custom-dir")
        parent.git.list_worktrees.return_value = [
            self.worktree_record("custom-dir", "refs/heads/feature/my-feature")
        ]

        RemoveCommand(runtime)("custom-dir", force=True, delete_branch=True)

        parent.git.remove_worktree.assert_called_once_with("custom-dir", force=True)
        parent.git.delete_branch.assert_called_once_with("feature/my-feature", force=True)
        deleted_names = [args.args[0] for args in parent.git.delete_branch.call_args_list]
        self.assertNotIn("custom-dir", deleted_names)

        def first_index(matched):
            for index, called in enumerate(parent.mock_calls):
                if called == matched:
                    return index
            self.fail(f"Missing expected call: {matched}")

        pre_remove = call.hooks.fire(
            Hook.pre_remove, "custom-dir", "custom-dir", bypass_allowlist=False
        )
        post_remove = call.hooks.fire(
            Hook.post_remove, "custom-dir", "custom-dir", bypass_allowlist=False
        )

        sequence = [
            first_index(pre_remove),
            first_index(call.git.list_worktrees()),
            first_index(call.git.remove_worktree("custom-dir", force=True)),
            first_index(call.git.delete_branch("feature/my-feature", force=True)),
            first_index(post_remove),
        ]
        self.assertEqual(sequence, sorted(sequence))

    def test_call__skips_branch_deletion_for_detached_worktree(self):
        self.add_worktree("detached")
        self.git.list_worktrees.return_value = [self.worktree_record("detached", None)]

        self.command("detached", delete_branch=True)

        self.git.remove_worktree.assert_called_once_with("detached", force=False)
        self.assert_skips_branch_deletion_with_warning()

    def test_call__skips_branch_deletion_when_worktree_record_is_missing(self):
        self.add_worktree("detached")
        self.git.list_worktrees.return_value = [self.worktree_record("other", "refs/heads/other")]

        self.command("detached", delete_branch=True)

        self.git.remove_worktree.assert_called_once_with("detached", force=False)
        self.assert_skips_branch_deletion_with_warning()

    def test_call__surfaces_worktree_listing_failure_before_removal(self):
        self.add_worktree("feature")
        self.git.list_worktrees.side_effect = CommandError("cannot list worktrees")

        with self.assertRaisesRegex(CommandError, "cannot list worktrees"):
            self.command("feature", delete_branch=True)

        self.git.remove_worktree.assert_not_called()
        self.hooks.fire.assert_called_once_with(
            Hook.pre_remove, "feature", "feature", bypass_allowlist=False
        )

    def test_call__does_not_list_worktrees_without_delete_branch(self):
        self.add_worktree("feature")

        self.command("feature")

        self.git.list_worktrees.assert_not_called()
        self.git.delete_branch.assert_not_called()
        self.logger.warning.assert_not_called()

    def test_call__warns_when_resolved_branch_deletion_fails(self):
        self.add_worktree("custom-dir")
        self.git.list_worktrees.return_value = [
            self.worktree_record("custom-dir", "refs/heads/feature/my-feature")
        ]
        self.git.delete_branch.side_effect = CommandError("boom")

        self.command("custom-dir", force=True, delete_branch=True)

        self.logger.warning.assert_called_once_with(
            "Failed to delete branch 'feature/my-feature': boom"
        )
        self.hooks.fire.assert_any_call(
            Hook.post_remove, "custom-dir", "custom-dir", bypass_allowlist=False
        )

    def test_call__logs_removal_milestone(self):
        self.add_worktree("feature")

        with patch("treefort.ui.is_color_enabled", return_value=False):
            self.command("feature")

        self.logger.info.assert_called_once_with("✓ Removed worktree feature")

    def test_call__logs_branch_deletion_milestone(self):
        self.add_worktree("feature")
        self.git.list_worktrees.return_value = [
            self.worktree_record("feature", "refs/heads/feature")
        ]

        with patch("treefort.ui.is_color_enabled", return_value=False):
            self.command("feature", delete_branch=True)

        self.assertEqual(
            self.logger.info.call_args_list,
            [call("✓ Removed worktree feature"), call("✓ Deleted branch feature")],
        )

    def test_call__logs_no_branch_milestone_when_deletion_fails(self):
        self.add_worktree("feature")
        self.git.list_worktrees.return_value = [
            self.worktree_record("feature", "refs/heads/feature")
        ]
        self.git.delete_branch.side_effect = CommandError("boom")

        with patch("treefort.ui.is_color_enabled", return_value=False):
            self.command("feature", delete_branch=True)

        self.assertEqual(self.logger.info.call_args_list, [call("✓ Removed worktree feature")])

    def test_call__yes_true_sets_bypass_allowlist_true(self):
        self.add_worktree("feature")

        self.command("feature", yes=True)

        self.hooks.fire.assert_any_call(
            Hook.pre_remove, "feature", "feature", bypass_allowlist=True
        )
        self.hooks.fire.assert_any_call(
            Hook.post_remove, "feature", "feature", bypass_allowlist=True
        )
