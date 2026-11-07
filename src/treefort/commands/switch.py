import sys
from pathlib import Path

from treefort import ui
from treefort.command import Command
from treefort.config import RepositoryConfig
from treefort.errors import WorktreeNotFoundError
from treefort.git import LOCAL_BRANCH_REF_PREFIX


class SwitchCommand(Command):
    """Switch the shell to another worktree in the current project"""

    _name = "switch"
    _aliases = ["sw"]

    def __call__(self, target: str):
        """
        Switch to another worktree in the current project.

        Accepts a worktree name (including nested paths like `subpath/feature`), the name of a
        branch that is checked out in a worktree, or one of the shortcuts: `-` for the previous
        worktree, `^` for the worktree on the default branch (or the project root), and `@` or
        `.` for the root of the current worktree (or the project root).

        When shell integration is active, the parent shell changes into the target directory;
        otherwise the target path is printed so it can be used with `cd`.

        Examples:
            treefort switch feature-a
            treefort switch subpath/feature
            treefort switch my-branch
            treefort switch -
            treefort switch ^
            treefort sw @

        :param target: The worktree name, branch name, or shortcut to switch to
        """
        self._context.assert_within_project()
        config = self._context.get_config()
        current_worktree = self._context.current_worktree_name()
        project_dir = self._context.project_dir

        # The `-` and `@` shortcuts resolve without a worktree listing, but the listing is
        # still needed to validate the current worktree before recording it as previous.
        worktrees: list[dict] = []
        if current_worktree is not None or target not in ("-", "@", "."):
            worktrees = self._runtime.git.list_worktrees()

        target_path, target_name = self._resolve_target(
            target, config, current_worktree, worktrees, project_dir
        )

        if (
            current_worktree is not None
            and current_worktree != target_name
            and self._is_active_worktree(current_worktree, worktrees, project_dir)
        ):
            config.update(last_worktree=current_worktree)
            self._context.set_config(config)

        if self._context.request_cd(target_path):
            self._logger.info(ui.success(f"Switched to {ui.bold(str(target_path))}"))
        else:
            # Keep stdout limited to the path so `cd "$(treefort switch ...)"` keeps working.
            print(str(target_path))
            print(
                ui.dim("For automatic directory switching, run `treefort shell-init`.", sys.stderr),
                file=sys.stderr,
            )

    def _resolve_target(
        self,
        target: str,
        config: RepositoryConfig,
        current_worktree: str | None,
        worktrees: list[dict],
        project_dir: Path,
    ) -> tuple[Path, str | None]:
        """Resolve a switch target to its path and project-relative worktree name.

        :param target: The worktree name, branch name, or shortcut to resolve
        :param config: The repository configuration
        :param current_worktree: The worktree the current directory is inside, if any
        :param worktrees: The project's worktree listing
        :param project_dir: The project directory
        :return: The target directory and its name relative to the project, or `None` when the
            target is the project root or a worktree outside the project
        :raises WorktreeNotFoundError: When the target cannot be resolved to an existing worktree
        """
        if target == "-":
            return self._resolve_previous(config, project_dir)
        if target in ("@", "."):
            return self._resolve_current(project_dir, current_worktree)
        if target == "^":
            return self._resolve_default_branch(config, worktrees, project_dir)
        return self._resolve_named(target, worktrees, project_dir)

    def _resolve_previous(
        self, config: RepositoryConfig, project_dir: Path
    ) -> tuple[Path, str | None]:
        """Resolve the previous worktree, which must still exist on disk."""
        last_worktree = config.last_worktree
        if not last_worktree or not (project_dir / last_worktree).is_dir():
            raise WorktreeNotFoundError("No previous worktree recorded")
        return (project_dir / last_worktree).resolve(), last_worktree

    def _resolve_current(
        self, project_dir: Path, current_worktree: str | None
    ) -> tuple[Path, str | None]:
        """Resolve the root of the worktree the current directory is inside."""
        if current_worktree is None:
            return project_dir, None
        return (project_dir / current_worktree).resolve(), current_worktree

    def _resolve_default_branch(
        self, config: RepositoryConfig, worktrees: list[dict], project_dir: Path
    ) -> tuple[Path, str | None]:
        """Resolve the worktree on the default branch, or the project root when there is none."""
        default_ref = f"{LOCAL_BRANCH_REF_PREFIX}{config.default_branch}"
        for worktree in worktrees:
            if worktree.get("is_bare") or worktree.get("branch") != default_ref:
                continue
            if (path := Path(worktree["path"])).is_dir():
                return path.resolve(), self._relative_name(path, project_dir)
        return project_dir, None

    def _resolve_named(
        self, target: str, worktrees: list[dict], project_dir: Path
    ) -> tuple[Path, str | None]:
        """Resolve a worktree name, then a checked-out branch name, to an existing worktree."""
        for worktree in worktrees:
            if worktree.get("is_bare") or not (path := Path(worktree["path"])).is_dir():
                continue
            if (name := self._relative_name(path, project_dir)) == target:
                return path.resolve(), name
        for worktree in worktrees:
            branch = worktree.get("branch")
            if worktree.get("is_bare") or branch != f"{LOCAL_BRANCH_REF_PREFIX}{target}":
                continue
            if (path := Path(worktree["path"])).is_dir():
                return path.resolve(), self._relative_name(path, project_dir)
        raise WorktreeNotFoundError(f"No worktree or branch named {target}")

    def _is_active_worktree(self, name: str, worktrees: list[dict], project_dir: Path) -> bool:
        """Whether `name` refers to a worktree that currently exists on disk.

        Guards against recording a directory that merely contains a nested git repository,
        such as a submodule, as the previous worktree.
        """
        for worktree in worktrees:
            if worktree.get("is_bare"):
                continue
            path = Path(worktree["path"])
            if path.is_dir() and self._relative_name(path, project_dir) == name:
                return True
        return False

    @staticmethod
    def _relative_name(path: Path, project_dir: Path) -> str | None:
        """Return the path relative to the project directory, or `None` when outside it."""
        resolved = path.resolve()
        if not resolved.is_relative_to(project_dir) or resolved == project_dir:
            return None
        return resolved.relative_to(project_dir).as_posix()
