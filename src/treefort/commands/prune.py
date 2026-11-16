from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from treefort.command import Arg, Command, command
from treefort.errors import CommandError
from treefort.hooks import Hook
from treefort.utils import normalize_worktree_name


@dataclass(frozen=True)
class PruneCandidate:
    """A worktree branch whose head matches a merged pull request."""

    worktree_name: str
    branch_name: str
    expected_head: str


@command("prune")
class PruneCommand(Command):
    """Prune worktrees whose associated PRs have been merged"""

    def __call__(
        self,
        *,
        force: Annotated[bool, Arg(short="-f")] = False,
        yes: Annotated[bool, Arg(short="-y")] = False,
    ):
        """
        Prune worktrees whose associated PRs have been merged.

        This command identifies worktrees linked to merged PRs and removes
        both the worktree directory and the local Git branch.

        Examples:
            treefort prune
            treefort prune --yes
            treefort prune --force --yes

        :param force: Whether to bypass git safety checks when removing the worktree
        :param yes: Perform any action automatically without asking (execute new or modified hooks)
        """
        self._context.assert_within_project()

        pruned_count = 0
        failed_count = 0

        for candidate in self._iter_worktrees():
            if not yes:
                response = input(
                    f"Remove worktree '{candidate.worktree_name}' and branch "
                    f"'{candidate.branch_name}'? (y/N): "
                )
                if response.lower() != "y":
                    self._logger.info(f"Skipping '{candidate.worktree_name}'")
                    continue

            if self._do_prune(candidate, force, yes):
                pruned_count += 1
            else:
                failed_count += 1

        if pruned_count == 0 and failed_count == 0:
            self._logger.info("No merged PR worktrees found to prune.")
        else:
            self._logger.info(f"Pruning complete: {pruned_count} pruned, {failed_count} failed.")

    def _iter_worktrees(self):
        """
        Filter worktrees and search for matching PRs, yield candidates for pruning.
        """
        project_dir = self._context.project_dir
        config = self._context.get_config()
        # A partial configuration cannot produce a usable repository identifier, so let gh infer
        # it from the current checkout instead of passing an identifier built from missing values.
        owner_repo = f"{config.owner}/{config.name}" if config.owner and config.name else None

        worktrees = self._runtime.git.list_worktrees()

        for wt in worktrees:
            if wt.get("is_bare") or Path(wt["path"]).resolve() == project_dir.resolve():
                continue

            branch_ref = wt.get("branch")
            if not branch_ref or not branch_ref.startswith("refs/heads/"):
                continue

            branch_name = branch_ref.removeprefix("refs/heads/")
            worktree_name = Path(wt["path"]).resolve().relative_to(project_dir.resolve()).as_posix()

            pr_infos = self._runtime.gh.merged_pr_by_head(branch_name, owner_repo=owner_repo)

            if not pr_infos:
                continue

            # Safety check: ensure local branch head matches the merged PR head
            try:
                local_head = self._runtime.git.get_branch_head(branch_name)
            except CommandError as e:
                self._logger.warning(f"Skipping '{worktree_name}': could not get branch head ({e})")
                continue

            pr_info = next((_pr for _pr in pr_infos if _pr.get("headRefOid") == local_head), None)

            if not pr_info:
                pr_heads = [pr["headRefOid"][:8] for pr in pr_infos if pr.get("headRefOid")]
                self._logger.warning(
                    f"Skipping '{worktree_name}': local branch head ({local_head[:8]}) does not "
                    f"match any merged PR head(s) ({','.join(pr_heads)}). "
                    "The branch may have been reused."
                )
                continue

            self._logger.info(f"Found merged PR for worktree '{worktree_name}':")
            self._logger.info(f"  PR #{pr_info['number']}: {pr_info['title']}")
            self._logger.info(f"  URL: {pr_info['url']}")

            yield PruneCandidate(worktree_name, branch_name, local_head)

    def _do_prune(self, candidate: PruneCandidate, force: bool, yes: bool) -> bool:
        """
        Executes the prune operation, firing the hooks before and after.

        The local branch is deleted only if it still matches the head verified against a merged
        PR by :meth:`_iter_worktrees`. ``force`` only applies to worktree removal, where git
        safety checks remain meaningful.
        """
        project_dir = self._context.project_dir

        try:
            normalized_name = normalize_worktree_name(candidate.worktree_name)
            with self._context.use(project_dir):
                self._runtime.hooks.fire(
                    Hook.pre_remove,
                    candidate.worktree_name,
                    normalized_name,
                    bypass_allowlist=yes,
                )

                self._runtime.git.remove_worktree(candidate.worktree_name, force=force)
                self._runtime.git.delete_branch_if_head(
                    candidate.branch_name, candidate.expected_head
                )

                self._runtime.hooks.fire(
                    Hook.post_remove,
                    candidate.worktree_name,
                    normalized_name,
                    bypass_allowlist=yes,
                )

            self._logger.info(f"Successfully pruned '{candidate.worktree_name}'")
            return True
        except CommandError as e:
            self._logger.error(f"Failed to prune '{candidate.worktree_name}': {e}")
            return False
