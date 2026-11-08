import inspect
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from unittest import TestCase
from unittest.mock import ANY, Mock, patch

from treefort.command import Arg, Completion
from treefort.commands.checkout import CheckoutCommand, CheckoutInput
from treefort.errors import BranchInputError, CommandError, RemoteNotFoundError
from treefort.git import GitRemote
from treefort.hooks import Hook


class StubContext:
    def __init__(self, project_dir, config):
        self.project_dir = project_dir
        self._config = config
        self.assert_called = False

    def assert_within_project(self):
        self.assert_called = True

    def get_config(self):
        return self._config

    @contextmanager
    def use(self, cwd):
        yield


class CheckoutInputTestCase(TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            url="https://github.com/Octo/Repo",
            owner="Octo",
            name="Repo",
        )
        self.context = StubContext(Path("/repo"), self.config)
        self.runtime = SimpleNamespace(
            context=self.context,
            get_remote=Mock(return_value=None),
        )

    def test_owner__pr_number_returns_config_owner(self):
        inpt = CheckoutInput(self.runtime, "1234")
        self.assertEqual(inpt.owner, "Octo")

    def test_owner__pr_url_matches_config_url_case_insensitively(self):
        inpt = CheckoutInput(self.runtime, "https://github.com/octo/repo/pull/1234")
        self.assertEqual(inpt.owner, "Octo")

    def test_owner__pr_url_extracts_fork_owner(self):
        inpt = CheckoutInput(self.runtime, "https://github.com/fork-owner/repo/pull/1234")
        self.assertEqual(inpt.owner, "fork-owner")

    def test_owner__pr_url_does_not_match_prefixed_repo_name_as_config_url(self):
        inpt = CheckoutInput(self.runtime, "https://github.com/octo/Repo-fork/pull/1234")
        self.assertEqual(inpt.owner, "octo")

    def test_owner__remote_name_extracts_owner_from_ssh_remote(self):
        self.runtime.get_remote.return_value = GitRemote(
            "upstream", "git@github.com:fork-owner/repo.git", "fetch"
        )
        inpt = CheckoutInput(self.runtime, "feature", remote_name="upstream")
        self.assertEqual(inpt.owner, "fork-owner")
        self.runtime.get_remote.assert_called_once_with(name="upstream")

    def test_owner__remote_name_extracts_owner_from_https_remote(self):
        self.runtime.get_remote.return_value = GitRemote(
            "upstream", "https://github.com/fork-owner/repo.git", "fetch"
        )
        inpt = CheckoutInput(self.runtime, "feature", remote_name="upstream")
        self.assertEqual(inpt.owner, "fork-owner")
        self.runtime.get_remote.assert_called_once_with(name="upstream")

    def test_owner__remote_name_raises_when_remote_not_found(self):
        self.runtime.get_remote.return_value = None
        inpt = CheckoutInput(self.runtime, "feature", remote_name="missing")
        with self.assertRaises(RemoteNotFoundError):
            _ = inpt.owner


class CheckoutCommandTestCase(TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            url="https://github.com/octo/repo",
            owner="octo",
            name="repo",
        )
        self.context = StubContext(Path("/repo"), self.config)
        self.hooks = SimpleNamespace(fire=Mock())
        self.git = SimpleNamespace(open_worktree=Mock(), fetch=Mock())
        self.gh = SimpleNamespace(pr_status=Mock())
        self.templates = SimpleNamespace(copy=Mock())
        self.runtime = SimpleNamespace(
            logger=Mock(),
            context=self.context,
            hooks=self.hooks,
            git=self.git,
            gh=self.gh,
            templates=self.templates,
            get_remote=Mock(return_value=GitRemote("origin", "uri", "fetch")),
            get_default_remote=Mock(return_value=GitRemote("origin", "uri", "fetch")),
        )
        self.command = CheckoutCommand(self.runtime)

    def test_call__uses_branch_name(self):
        self.command("feature")

        self.assertTrue(self.context.assert_called)

        self.git.fetch.assert_called_once_with("origin", "feature:feature")
        self.runtime.get_remote.assert_called_once_with(owner_name="octo")
        self.git.open_worktree.assert_called_once_with("feature")
        self.templates.copy.assert_called_once_with("feature")
        self.hooks.fire.assert_any_call(
            Hook.pre_checkout, "feature", ANY, "feature", bypass_allowlist=False
        )
        self.hooks.fire.assert_any_call(
            Hook.post_checkout, "feature", ANY, "feature", bypass_allowlist=False
        )

    def test_call__uses_remote_and_branch_name(self):
        self.command("feature", remote="aremote")

        self.assertTrue(self.context.assert_called)

        self.git.fetch.assert_called_once_with("aremote", "feature:feature")
        self.git.open_worktree.assert_called_once_with("feature")
        self.templates.copy.assert_called_once_with("feature")
        self.hooks.fire.assert_any_call(
            Hook.pre_checkout, "feature", ANY, "feature", bypass_allowlist=False
        )
        self.hooks.fire.assert_any_call(
            Hook.post_checkout, "feature", ANY, "feature", bypass_allowlist=False
        )

    def test_call__uses_pr_number(self):
        self.gh.pr_status.return_value = {
            "headRefName": "feature",
        }

        self.command("1234")

        self.assertTrue(self.context.assert_called)
        self.gh.pr_status.assert_called_once_with("1234", owner_repo="octo/repo")
        self.runtime.get_remote.assert_called_once_with(owner_name="octo")
        self.git.open_worktree.assert_called_once_with("feature")
        self.templates.copy.assert_called_once_with("feature")
        self.hooks.fire.assert_any_call(
            Hook.pre_checkout, "feature", ANY, "feature", bypass_allowlist=False
        )
        self.hooks.fire.assert_any_call(
            Hook.post_checkout, "feature", ANY, "feature", bypass_allowlist=False
        )

    def test_call__uses_pr_url(self):
        self.gh.pr_status.return_value = {
            "headRefName": "feature",
        }

        self.command("https://github.com/octo/repo/pull/1234")

        self.assertTrue(self.context.assert_called)
        self.gh.pr_status.assert_called_once_with("1234", owner_repo="octo/repo")
        self.runtime.get_remote.assert_called_once_with(owner_name="octo")
        self.git.open_worktree.assert_called_once_with("feature")
        self.templates.copy.assert_called_once_with("feature")
        self.hooks.fire.assert_any_call(
            Hook.pre_checkout, "feature", ANY, "feature", bypass_allowlist=False
        )
        self.hooks.fire.assert_any_call(
            Hook.post_checkout, "feature", ANY, "feature", bypass_allowlist=False
        )

    def test_call__raises_on_missing_remote(self):
        self.runtime.get_remote.return_value = None

        with self.assertRaises(RemoteNotFoundError, msg="Couldn't find matching remote"):
            self.command("feature")

    def test_call__rejects_invalid_pull_url(self):
        with self.assertRaises(BranchInputError, msg="Couldn't parse input. Must be"):
            self.command("https://github.com/octo/repo/issues/12")

    def test_call__runs_hooks_outside_spinner(self):
        events = []
        self.hooks.fire.side_effect = lambda hook, *args, **kwargs: events.append(hook.name)

        with patch("treefort.commands.checkout.ui.spinner") as spinner:
            spinner.return_value.__exit__.side_effect = lambda *args: events.append("exit")
            self.command("feature")

        self.assertEqual(events, ["exit", "pre_checkout", "post_checkout"])

    def test_call__yes_true_sets_bypass_allowlist_true(self):
        self.gh.pr_status.return_value = {
            "headRefName": "feature",
        }

        self.command("1234", yes=True)

        self.hooks.fire.assert_any_call(
            Hook.pre_checkout, "feature", ANY, "feature", bypass_allowlist=True
        )
        self.hooks.fire.assert_any_call(
            Hook.post_checkout, "feature", ANY, "feature", bypass_allowlist=True
        )

    def test_call__ignores_fetch_error(self):
        self.git.fetch.side_effect = CommandError("fetch failed")

        self.command("feature")

        self.git.open_worktree.assert_called_once_with("feature")
        self.runtime.logger.warning.assert_called_once_with(
            "Ignoring git fetch error, attempting to open worktree"
        )

    def test_call__logs_fetch_failure_at_debug(self):
        self.git.fetch.side_effect = CommandError("fetch failed")

        self.command("feature")

        self.runtime.logger.debug.assert_called_once_with("Ignoring fetch failure: fetch failed")

    def test_call__warns_outside_spinner_when_fetch_fails(self):
        events = []
        self.git.fetch.side_effect = CommandError("fetch failed")
        self.runtime.logger.warning.side_effect = lambda *args, **kwargs: events.append("warning")

        with patch("treefort.commands.checkout.ui.spinner") as spinner:
            spinner.return_value.__exit__.side_effect = lambda *args: events.append("exit")
            self.command("feature")

        self.assertEqual(events, ["exit", "warning"])

    def test_call__logs_completion_milestone(self):
        with patch("treefort.ui.is_color_enabled", return_value=False):
            self.command("feature")

        self.runtime.logger.info.assert_called_once_with(
            "✓ Checked out worktree feature at /repo/feature"
        )

    def test_call__spins_while_fetching(self):
        with patch("treefort.commands.checkout.ui.spinner") as spinner:
            self.command("feature")

        spinner.assert_called_once_with("Fetching feature...", enabled=True)

    def test_call__resolves_pr_inside_spinner(self):
        events = []

        def record_pr_status(*args, **kwargs):
            events.append("pr_status")
            return {"headRefName": "feature"}

        self.gh.pr_status.side_effect = record_pr_status

        with patch("treefort.commands.checkout.ui.spinner") as spinner:
            spinner.return_value.__enter__.side_effect = lambda *args: events.append("enter")
            spinner.return_value.__exit__.side_effect = lambda *args: events.append("exit")
            self.command("1234")

        self.assertEqual(events, ["enter", "pr_status", "exit"])
        spinner.assert_called_once_with("Fetching 1234...", enabled=True)

    def test_call__disables_spinner_when_verbose(self):
        self.runtime.verbose = True

        with patch("treefort.commands.checkout.ui.spinner") as spinner:
            self.command("feature")

        spinner.assert_called_once_with("Fetching feature...", enabled=False)

    def test_call__declares_the_cli_contract(self):
        parameters = inspect.signature(CheckoutCommand.__call__).parameters

        self.assertIs(inspect.Parameter.POSITIONAL_OR_KEYWORD, parameters["branch_or_pr"].kind)
        self.assertEqual(
            (str, Arg(complete=Completion.BRANCH_REF)),
            get_args(parameters["branch_or_pr"].annotation),
        )
        self.assertIs(inspect.Parameter.KEYWORD_ONLY, parameters["remote"].kind)
        self.assertEqual(
            (str | None, Arg(short="-r", complete=Completion.REMOTE)),
            get_args(parameters["remote"].annotation),
        )
        self.assertIs(inspect.Parameter.KEYWORD_ONLY, parameters["yes"].kind)
        self.assertEqual((bool, Arg(short="-y")), get_args(parameters["yes"].annotation))
