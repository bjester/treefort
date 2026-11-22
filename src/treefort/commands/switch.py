import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from treefort import ui
from treefort.command import Arg, Command, Completion, command
from treefort.config import RepositoryConfig
from treefort.errors import WorktreeNotFoundError
from treefort.git import LOCAL_BRANCH_REF_PREFIX

PREVIOUS_TARGET = "-"
DEFAULT_BRANCH_TARGET = "^"
CURRENT_TARGETS = ("@", ".")


@dataclass(frozen=True)
class ListedWorktree:
    """A managed worktree normalized from a ``git worktree list`` entry."""

    name: str
    path: Path
    branch: str | None


@command("switch", aliases=("sw",))
class SwitchCommand(Command):
    """Switch to another worktree in the current project"""

    def __call__(
        self,
        target: Annotated[str, Arg(complete=Completion.WORKTREE_SWITCH)],
    ) -> None:
        """Switch to another worktree in the current project.

        Accepts a project-relative worktree name, a checked-out branch name, or a shortcut:
        `-` for the previous worktree, `^` for the default-branch worktree (or project root),
        and `@` or `.` for the current worktree root (or project root).

        :param target: The worktree name, branch name, or shortcut (`-`, `^`, `@`, `.`)
        """
        self._context.assert_within_project()
        project_dir = self._context.project_dir.resolve()
        cwd = self._context.cwd.resolve()
        config = self._context.get_config()
        raw_worktrees = self._runtime.git.list_worktrees()

        listed = self._list_managed_worktrees(raw_worktrees, project_dir)
        current = self._find_current_worktree(cwd, listed)
        target_path, target_name = self._resolve_target(
            target, current, listed, raw_worktrees, config, project_dir
        )
        self._emit_switch_result(target_path, target_name, current, config)

    def _list_managed_worktrees(
        self, raw_worktrees: list[dict], project_dir: Path
    ) -> list[ListedWorktree]:
        """Normalize the non-bare worktrees that live inside the project directory."""
        listed = []
        for entry in raw_worktrees:
            if entry.get("is_bare"):
                continue
            raw_path = entry.get("path")
            if not raw_path:
                continue
            path = Path(raw_path).resolve()
            if path == project_dir or path == project_dir / ".bare":
                continue
            if not path.is_dir() or not path.is_relative_to(project_dir):
                continue
            listed.append(
                ListedWorktree(
                    name=path.relative_to(project_dir).as_posix(),
                    path=path,
                    branch=entry.get("branch"),
                )
            )
        return listed

    def _find_current_worktree(
        self, cwd: Path, listed: list[ListedWorktree]
    ) -> ListedWorktree | None:
        """Return the deepest managed worktree containing ``cwd``.

        Matching against the listed worktrees resolves a submodule or nested repository to the
        managed worktree around it, so neither ever reaches ``last_worktree``.
        """
        matches = [entry for entry in listed if cwd == entry.path or cwd.is_relative_to(entry.path)]
        if not matches:
            return None
        return max(matches, key=lambda entry: len(entry.path.parts))

    def _resolve_target(
        self,
        target: str,
        current: ListedWorktree | None,
        listed: list[ListedWorktree],
        raw_worktrees: list[dict],
        config: RepositoryConfig,
        project_dir: Path,
    ) -> tuple[Path, str | None]:
        """Resolve a target token to a directory and its managed worktree name."""
        if target == PREVIOUS_TARGET:
            return self._resolve_previous_target(config, listed)
        if target in CURRENT_TARGETS:
            if current is None:
                return project_dir, None
            return current.path, current.name
        if target == DEFAULT_BRANCH_TARGET:
            return self._resolve_default_branch_target(config, listed, raw_worktrees, project_dir)
        return self._resolve_named_target(target, listed)

    def _resolve_previous_target(
        self, config: RepositoryConfig, listed: list[ListedWorktree]
    ) -> tuple[Path, str | None]:
        """Resolve ``-`` to the worktree recorded as most recently switched away from."""
        last = config.last_worktree
        if last is None:
            raise WorktreeNotFoundError("No previous worktree recorded")
        for entry in listed:
            if entry.name == last:
                return entry.path, entry.name
        raise WorktreeNotFoundError(f"Previous worktree '{last}' is not a worktree in this project")

    def _resolve_default_branch_target(
        self,
        config: RepositoryConfig,
        listed: list[ListedWorktree],
        raw_worktrees: list[dict],
        project_dir: Path,
    ) -> tuple[Path, str | None]:
        """Resolve ``^`` to the default-branch worktree, or to the project root."""
        branch_ref = f"{LOCAL_BRANCH_REF_PREFIX}{config.default_branch}"
        for entry in listed:
            if entry.branch == branch_ref:
                return entry.path, entry.name
        for entry in raw_worktrees:
            if entry.get("is_bare") or entry.get("branch") != branch_ref:
                continue
            raw_path = entry.get("path")
            if not raw_path:
                continue
            path = Path(raw_path).resolve()
            if path.is_dir():
                return path, None
        return project_dir, None

    def _resolve_named_target(
        self, target: str, listed: list[ListedWorktree]
    ) -> tuple[Path, str | None]:
        """Resolve a worktree name or checked-out branch name."""
        lookup = target.rstrip("/") or target
        for entry in listed:
            if entry.name == lookup:
                return entry.path, entry.name
        branch_ref = f"{LOCAL_BRANCH_REF_PREFIX}{lookup}"
        for entry in listed:
            if entry.branch == branch_ref:
                return entry.path, entry.name
        raise WorktreeNotFoundError(f"No worktree or checked-out branch named '{target}'")

    def _emit_switch_result(
        self,
        target_path: Path,
        target_name: str | None,
        current: ListedWorktree | None,
        config: RepositoryConfig,
    ) -> None:
        """Record the worktree being left and deliver the directory change directive."""
        if current is not None and current.name != target_name:
            config.update(last_worktree=current.name)
            self._context.set_config(config)

        if self._context.request_cd(target_path):
            self._logger.info(ui.success(f"Switched to {ui.bold(str(target_path))}"))
            return

        # The raw path goes to stdout so `cd "$(treefort switch <target>)"` works, and so the
        # logger never rewrites $HOME into ~/ inside a path meant to be pasted
        print(str(target_path))
        if not self._context.is_cd_enabled():
            hint = "For automatic directory switching, run `treefort install --shell`."
            sys.stderr.write(f"{ui.dim(hint, sys.stderr)}\n")
