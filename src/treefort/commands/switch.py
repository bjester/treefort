import sys
from dataclasses import dataclass
from pathlib import Path

from treefort import ui
from treefort.command import Command
from treefort.config import RepositoryConfig
from treefort.errors import WorktreeNotFoundError
from treefort.git import LOCAL_BRANCH_REF_PREFIX


@dataclass(frozen=True)
class ListedWorktree:
    """A worktree from `git worktree list` that exists on disk.

    :param name: The worktree root relative to the project directory, or ``None`` when it is
        outside the project
    :param path: The resolved worktree root
    :param branch: The fully qualified branch checked out in the worktree, if any
    """

    name: str | None
    path: Path
    branch: str | None


class SwitchCommand(Command):
    """Switch the shell to another worktree in the current project"""

    _name = "switch"
    _aliases = ["sw"]
    _literal_args = ("target",)

    def __call__(self, target: str):
        """
        Switch to another worktree in the current project.

        Accepts a worktree name (including nested paths like `subpath/feature`), the name of a
        branch that is checked out in a worktree, or one of the shortcuts: `-` for the previous
        worktree, `^` for the worktree on the default branch (or the project root), and `@` or
        `.` for the root of the current worktree (or the project root).

        When the shell wrapper installed by `treefort shell-init` is active and the directive
        can be written, it changes into the target directory; otherwise the target path is
        printed so it can be used with `cd`.

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

        # `@`/`.` resolve from the current directory alone; every other target needs the
        # worktree listing, both to resolve it and to validate the previous worktree before
        # returning to it or recording the current one.
        listed: list[ListedWorktree] = []
        if target not in ("@", "."):
            listed = self._listed_worktrees(self._runtime.git.list_worktrees(), project_dir)

        target_path, target_name = self._resolve_target(
            target, config, current_worktree, listed, project_dir
        )

        if (
            current_worktree is not None
            and current_worktree != target_name
            and self._find_worktree(listed, current_worktree) is not None
        ):
            config.update(last_worktree=current_worktree)
            self._context.set_config(config)

        if self._context.request_cd(target_path):
            self._logger.info(ui.success(f"Switched to {ui.bold(str(target_path))}"))
        else:
            # Keep stdout limited to the path so `cd "$(treefort switch ...)"` keeps working.
            print(str(target_path))
            if not self._context.shell_integration_active:
                print(
                    ui.dim(
                        "For automatic directory switching, run `treefort shell-init`.",
                        sys.stderr,
                    ),
                    file=sys.stderr,
                )

    def _resolve_target(
        self,
        target: str,
        config: RepositoryConfig,
        current_worktree: str | None,
        listed: list[ListedWorktree],
        project_dir: Path,
    ) -> tuple[Path, str | None]:
        """Resolve a switch target to its path and project-relative worktree name.

        :param target: The worktree name, branch name, or shortcut to resolve
        :param config: The repository configuration
        :param current_worktree: The worktree the current directory is inside, if any
        :param listed: The project's worktrees that exist on disk
        :param project_dir: The project directory
        :return: The target directory and its name relative to the project, or `None` when the
            target is the project root or a worktree outside the project
        :raises WorktreeNotFoundError: When the target cannot be resolved to an existing worktree
        """
        if target == "-":
            return self._resolve_previous(config, project_dir, listed)
        if target in ("@", "."):
            return self._resolve_current(project_dir, current_worktree)
        if target == "^":
            return self._resolve_default_branch(config, listed, project_dir)
        return self._resolve_named(target, listed)

    def _resolve_previous(
        self, config: RepositoryConfig, project_dir: Path, listed: list[ListedWorktree]
    ) -> tuple[Path, str | None]:
        """Resolve the previous worktree, which must still be an existing worktree.

        The recorded name is re-validated against the project's worktree listing so a
        hand-edited or corrupted config cannot direct the shell outside the project or into
        a directory that is not a worktree, such as `.bare`, a nested repository, or a parent
        directory left behind by a removed nested worktree.
        """
        last_worktree = config.last_worktree
        if not last_worktree:
            raise WorktreeNotFoundError("No previous worktree recorded")
        relative_name = self._relative_name(project_dir / last_worktree, project_dir)
        previous = self._find_worktree(listed, relative_name) if relative_name else None
        if previous is None:
            raise WorktreeNotFoundError(
                f"Previous worktree {last_worktree} is not a worktree in this project"
            )
        return previous.path, previous.name

    def _resolve_current(
        self, project_dir: Path, current_worktree: str | None
    ) -> tuple[Path, str | None]:
        """Resolve the root of the worktree the current directory is inside."""
        if current_worktree is None:
            return project_dir, None
        return (project_dir / current_worktree).resolve(), current_worktree

    def _resolve_default_branch(
        self, config: RepositoryConfig, listed: list[ListedWorktree], project_dir: Path
    ) -> tuple[Path, str | None]:
        """Resolve the worktree on the default branch, or the project root when there is none."""
        default_ref = f"{LOCAL_BRANCH_REF_PREFIX}{config.default_branch}"
        for worktree in listed:
            if worktree.branch == default_ref:
                return worktree.path, worktree.name
        return project_dir, None

    def _resolve_named(self, target: str, listed: list[ListedWorktree]) -> tuple[Path, str | None]:
        """Resolve a worktree name, then a checked-out branch name, to an existing worktree."""
        for worktree in listed:
            if worktree.name == target:
                return worktree.path, worktree.name
        branch_ref = f"{LOCAL_BRANCH_REF_PREFIX}{target}"
        for worktree in listed:
            if worktree.branch == branch_ref:
                return worktree.path, worktree.name
        raise WorktreeNotFoundError(f"No worktree or branch named {target}")

    def _listed_worktrees(self, worktrees: list[dict], project_dir: Path) -> list[ListedWorktree]:
        """Normalize `git worktree list` records into the worktrees that exist on disk.

        Skips the bare repository and worktrees whose directory is gone, so later lookups
        cannot resolve to a path that is no longer there.
        """
        listed = []
        for worktree in worktrees:
            if worktree.get("is_bare"):
                continue
            path = Path(worktree["path"])
            if not path.is_dir():
                continue
            listed.append(
                ListedWorktree(
                    name=self._relative_name(path, project_dir),
                    path=path.resolve(),
                    branch=worktree.get("branch"),
                )
            )
        return listed

    @staticmethod
    def _find_worktree(listed: list[ListedWorktree], name: str | None) -> ListedWorktree | None:
        """The listed worktree with the given project-relative name, if any."""
        return next((worktree for worktree in listed if worktree.name == name), None)

    @staticmethod
    def _relative_name(path: Path, project_dir: Path) -> str | None:
        """Return the path relative to the project directory, or `None` when outside it."""
        resolved = path.resolve()
        resolved_project = project_dir.resolve()
        if not resolved.is_relative_to(resolved_project) or resolved == resolved_project:
            return None
        return resolved.relative_to(resolved_project).as_posix()
