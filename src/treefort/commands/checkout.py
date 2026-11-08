import re
from functools import cached_property
from typing import Annotated

from treefort import ui
from treefort.command import Arg, Command, Completion, command
from treefort.errors import BranchInputError, CommandError, RemoteNotFoundError, RemoteUsageError
from treefort.git import GitUri
from treefort.hooks import Hook
from treefort.runtime import Runtime
from treefort.utils import normalize_worktree_name

URL_RE = re.compile(r"^https://github\.com/[a-z0-9-]+/[\w.-]+/pull/\d+$", re.IGNORECASE)
BRANCH_RE = re.compile(r"^[\w.-/]+$", re.IGNORECASE)


class CheckoutInput:
    """Checkout command input abstraction"""

    def __init__(self, runtime: Runtime, branch_or_pr: str, remote_name: str | None = None):
        self.runtime = runtime
        self.branch_or_pr = branch_or_pr
        self.remote_name = remote_name

    def validate(self):
        if (
            not self.branch_or_pr.isdigit()
            and not URL_RE.match(self.branch_or_pr)
            and not BRANCH_RE.match(self.branch_or_pr)
        ):
            raise BranchInputError(
                "Couldn't parse input. Must be branch name, PR number or PR URL."
            )

        if self.remote_name is not None and (
            self.branch_or_pr.isdigit() or URL_RE.match(self.branch_or_pr)
        ):
            raise RemoteUsageError("Remote name isn't allowed with PR number or PR URL.")

    @cached_property
    def owner(self) -> str:
        """The Github owner of the repository"""
        config = self.runtime.context.get_config()
        config_url_prefix = f"{config.url.rstrip('/').lower()}/"
        if self.branch_or_pr.isdigit() or self.branch_or_pr.lower().startswith(config_url_prefix):
            return config.owner
        elif URL_RE.match(self.branch_or_pr):
            return GitUri(self.branch_or_pr).owner
        elif not self.remote_name:
            return config.owner

        remote = self.runtime.get_remote(name=self.remote_name)
        if not remote:
            raise RemoteNotFoundError("Couldn't find matching remote for --remote input")

        return remote.owner

    @cached_property
    def remote(self) -> str:
        """The local name of the remote to use"""
        if self.remote_name:
            return self.remote_name
        remote = self.runtime.get_remote(owner_name=self.owner)
        if not remote:
            raise RemoteNotFoundError("Couldn't find matching remote for PR")
        return remote.name

    @cached_property
    def pr_number(self) -> str | None:
        """The PR number, if applicable"""
        if self.branch_or_pr.isdigit():
            return self.branch_or_pr

        if URL_RE.match(self.branch_or_pr):
            _, pr_number = self.branch_or_pr.rsplit("/", 1)
            return pr_number

        return None

    @cached_property
    def worktree_name(self) -> str:
        """The name of the worktree to create"""
        if self.pr_number:
            config = self.runtime.context.get_config()
            pr_status = self.runtime.gh.pr_status(
                self.pr_number, owner_repo=f"{self.owner}/{config.name}"
            )
            return pr_status["headRefName"]

        return self.branch_or_pr


@command("checkout")
class CheckoutCommand(Command):
    def __call__(
        self,
        branch_or_pr: Annotated[str, Arg(complete=Completion.BRANCH_REF)],
        *,
        remote: Annotated[str | None, Arg(short="-r", complete=Completion.REMOTE)] = None,
        yes: Annotated[bool, Arg(short="-y")] = False,
    ):
        """
        Checkout an existing branch or PR as a worktree.

        This accepts a PR number which will be looked up on the GitHub repository the project was
        initialized with. It will also accept a GitHub PR URL. Lastly, it will accept a branch
        name, if the branch is already tracked in your local repository. You may specify a remote
        to fetch the latest version of the branch with `--remote`, and that flag only works when
        passing a branch name.

        Examples:
            treefort checkout 1234
            treefort checkout https://github.com/octo/repo/pull/1234
            treefort checkout my-local-branch
            treefort checkout a-branch --remote upstream

        :param branch_or_pr: The branch, PR number, or PR URL to create as a worktree
        :param remote: If `branch_or_pr` is a branch, this may be set to choose the remote to use
        :param yes: Perform any action automatically without asking (execute new or modified hooks)
        :type yes: bool
        """
        self._context.assert_within_project()

        inpt = CheckoutInput(self._runtime, str(branch_or_pr), remote_name=remote)
        inpt.validate()

        fetch_error = False
        with ui.spinner(f"Fetching {inpt.branch_or_pr}...", enabled=self._ui_enabled):
            if inpt.pr_number:
                self._runtime.git.fetch(
                    inpt.remote, f"pull/{inpt.pr_number}/head:{inpt.worktree_name}"
                )
            else:
                # TODO: is this good to detect if it's local only?
                try:
                    self._runtime.git.fetch(
                        inpt.remote, f"{inpt.worktree_name}:{inpt.worktree_name}"
                    )
                except (CommandError, OSError) as e:
                    fetch_error = True
                    self._logger.debug(f"Ignoring fetch failure: {e}")

        if fetch_error:
            self._logger.warning("Ignoring git fetch error, attempting to open worktree")

        normalized_name = normalize_worktree_name(inpt.worktree_name)

        with self._context.use(self._context.project_dir):
            self._runtime.hooks.fire(
                Hook.pre_checkout,
                inpt.worktree_name,
                f"{inpt.remote}/{inpt.worktree_name}",
                normalized_name,
                bypass_allowlist=yes,
            )
            self._runtime.git.open_worktree(inpt.worktree_name)
            self._runtime.templates.copy(inpt.worktree_name)
            self._runtime.hooks.fire(
                Hook.post_checkout,
                inpt.worktree_name,
                f"{inpt.remote}/{inpt.worktree_name}",
                normalized_name,
                bypass_allowlist=yes,
            )

            self._logger.info(
                ui.success(
                    f"Checked out worktree {ui.bold(inpt.worktree_name)} "
                    f"at {ui.dim(str(self._context.project_dir / inpt.worktree_name))}"
                )
            )
