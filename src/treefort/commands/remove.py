from pathlib import Path

from treefort import ui
from treefort.command import Command
from treefort.errors import CommandError, WorktreeNotFoundError
from treefort.git import LOCAL_BRANCH_REF_PREFIX
from treefort.hooks import Hook
from treefort.utils import normalize_worktree_name


class RemoveCommand(Command):
    """Remove a worktree from the current project"""

    _name = "remove"
    _aliases = ["rm"]

    def __call__(
        self,
        worktree_name: str,
        force: bool = False,
        yes: bool = False,
        delete_branch: bool = False,
    ):
        """
        Remove a worktree from the current project that was added with `create` or `checkout`.

        If git detects the worktree has commits that are unmerged, then it will refuse to delete it.
        You may use `--force` to passthrough `--force` to git and force the worktree's deletion.

        Examples:
            treefort remove testing-create
            treefort remove testing-create --force
            treefort remove testing-create --delete-branch

        :param worktree_name: The name of the worktree to remove
        :param force: Whether to force the removal of the worktree, if it's unmerged
        :param yes: Perform any action automatically without asking
            (execute new or modified hooks)
        :param delete_branch: Whether to also delete the local Git branch associated with the
            worktree, which is not necessarily named after the worktree directory
        """
        project_dir = self._context.project_dir
        worktree_path = project_dir / worktree_name
        if not worktree_path.exists():
            raise WorktreeNotFoundError(f"Worktree {worktree_name} does not exist")

        normalized_name = normalize_worktree_name(worktree_name)
        branch_name: str | None = None

        with self._context.use(project_dir):
            self._runtime.hooks.fire(
                Hook.pre_remove, worktree_name, normalized_name, bypass_allowlist=yes
            )

            if delete_branch:
                branch_name = self._resolve_branch_name(worktree_path)

            self._runtime.git.remove_worktree(worktree_name, force=force)

            deleted_branch: str | None = None
            if delete_branch:
                if branch_name is None:
                    self._logger.warning(
                        f"Skipping branch deletion for worktree '{worktree_name}': "
                        "no associated local branch found."
                    )
                else:
                    try:
                        self._runtime.git.delete_branch(branch_name, force=force)
                    except CommandError as e:
                        self._logger.warning(f"Failed to delete branch '{branch_name}': {e}")
                    else:
                        deleted_branch = branch_name

            self._runtime.hooks.fire(
                Hook.post_remove, worktree_name, normalized_name, bypass_allowlist=yes
            )

            self._logger.info(ui.success(f"Removed worktree {ui.bold(worktree_name)}"))
            if deleted_branch:
                self._logger.info(ui.success(f"Deleted branch {ui.bold(deleted_branch)}"))

    def _resolve_branch_name(self, worktree_path: Path) -> str | None:
        """Return the name of the local branch checked out in the worktree at the given path.

        The branch is looked up before the worktree is removed, since a removed worktree no
        longer appears in the worktree listing. Returns `None` when the target worktree cannot
        be found or has no deletable local branch ref, such as for detached HEAD worktrees.

        :param worktree_path: The expected path of the target worktree
        :return: The branch name without the `refs/heads/` prefix, or `None` if unavailable
        """
        resolved_path = worktree_path.resolve()
        for worktree in self._runtime.git.list_worktrees():
            worktree_path_str = worktree.get("path")
            if not worktree_path_str or Path(worktree_path_str).resolve() != resolved_path:
                continue

            branch_ref = worktree.get("branch")
            if branch_ref and branch_ref.startswith(LOCAL_BRANCH_REF_PREFIX):
                return branch_ref[len(LOCAL_BRANCH_REF_PREFIX) :] or None
            return None
        return None
