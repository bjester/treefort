import logging
import os
import shlex
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

from treefort import ui
from treefort.config import (
    ConfigManager,
    ConfigProtocol,
    GlobalConfig,
    RepositoryConfig,
)
from treefort.errors import AncestorNotFoundError, ProjectNotFoundError
from treefort.utils import find_up

# The shell wrapper passes a temporary file path here, and treefort writes the directory to
# change into. The wrapper, not treefort, performs the ``cd`` because a child process cannot
# change its parent shell's working directory.
CD_FILE_ENV_VAR = "TREEFORT_CD_FILE"
GITDIR_PREFIX = "gitdir:"


def migrate_old_config(old_dir: Path, new_dir: Path) -> bool:
    """
    Migrate configuration from old directory to new directory.

    :param old_dir: The old configuration directory path
    :param new_dir: The new configuration directory path
    :return: True if migration occurred, False otherwise
    """
    if not old_dir.exists():
        return False

    # Create new directory
    new_dir.mkdir(parents=True, exist_ok=True)

    # Copy all contents from old to new
    for item in old_dir.iterdir():
        src = item
        dst = new_dir / item.name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    # Remove old directory if empty or after successful migration
    try:
        # Try to remove the old directory and its parents if they become empty
        shutil.rmtree(old_dir)
        # Clean up parent .gh directory if it's now empty
        parent_gh = old_dir.parent
        if parent_gh.exists() and parent_gh.is_dir() and not any(parent_gh.iterdir()):
            parent_gh.rmdir()
    except OSError:
        # Directory not empty or other error, leave it
        pass

    return True


def _gitdir_target(worktree_dir: Path) -> Path | None:
    """Return the resolved gitdir target recorded in a worktree's ``.git`` file.

    :param worktree_dir: The directory that may hold a ``.git`` file
    :return: The resolved target, or ``None`` when there is no readable ``gitdir:`` pointer
    """
    git_file = worktree_dir / ".git"
    if not git_file.is_file():
        return None
    try:
        content = git_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith(GITDIR_PREFIX):
            continue
        raw_target = stripped[len(GITDIR_PREFIX) :].strip()
        if not raw_target:
            return None
        return (worktree_dir / raw_target).resolve()
    return None


class Context:
    def __init__(self):
        self.cwd = Path.cwd()
        self._cached_project_dir: Path | None = None
        self._migrated = False
        self._config_manager = ConfigManager()

    @property
    def project_dir(self) -> Path:
        if self._cached_project_dir is None:
            git_bare_dir = find_up(".bare", self.cwd)
            self._cached_project_dir = git_bare_dir.parent
        return self._cached_project_dir

    @property
    def config_dir(self) -> Path:
        return self.project_dir / ".treefort"

    @property
    def global_config_dir(self) -> Path:
        try:
            parent_dir = self.project_dir.parent
        except AncestorNotFoundError:
            parent_dir = self.cwd.parent

        try:
            return find_up(".treefort", parent_dir)
        except AncestorNotFoundError:
            # default to ~/.treefort
            return Path.home() / ".treefort"

    @contextmanager
    def use(self, cwd: str | Path, *, end_at: str | Path | None = None):
        """Temporarily change the working directory for the duration of the block.

        The original directory is restored afterwards. When the block deleted the original
        directory, such as ``remove`` or ``prune`` removing the worktree the command started
        in, the context recovers in ``end_at`` and asks the parent shell to follow.

        :param cwd: The directory to enter
        :param end_at: The directory to recover in when the original one no longer exists,
            defaulting to the directory that was entered
        """
        old_cwd = self.cwd
        cwd_path = Path(cwd).resolve()
        os.chdir(cwd_path)
        self.cwd = cwd_path
        try:
            yield
        finally:
            if old_cwd.exists():
                os.chdir(old_cwd)
                self.cwd = old_cwd
            else:
                self._recover_cwd(cwd_path, end_at)

    def _recover_cwd(self, cwd_path: Path, end_at: str | Path | None) -> None:
        """Move to a surviving directory and ask the parent shell to follow.

        The recovery hint is written straight to ``sys.stderr`` because ``ColorFormatter``
        rewrites ``$HOME/`` into ``~/``, which breaks a quoted path meant to be pasted.
        """
        fallback_dir = Path(end_at).resolve() if end_at is not None else cwd_path
        try:
            os.chdir(fallback_dir)
        except OSError as e:
            # Raising from a finally block would replace the error that ended the block, so
            # report the failed recovery and leave the process where it is
            logging.getLogger("treefort").warning(
                f"Could not recover the working directory in {fallback_dir}: {e}"
            )
            return
        self.cwd = fallback_dir
        if self.request_cd(fallback_dir):
            return
        quoted = shlex.quote(str(fallback_dir))
        message = f"▲ Working directory was removed. Run: cd -- {quoted}"
        sys.stderr.write(f"{ui.yellow(message, sys.stderr)}\n")

    def is_cd_enabled(self) -> bool:
        """Return whether a shell wrapper is waiting for a directory change directive."""
        return bool(os.environ.get(CD_FILE_ENV_VAR))

    def request_cd(self, target_dir: Path) -> bool:
        """Ask the parent shell wrapper to change into ``target_dir``.

        :param target_dir: The directory the shell should enter
        :return: Whether the directive was delivered to the wrapper
        """
        cd_file = os.environ.get(CD_FILE_ENV_VAR)
        if not cd_file:
            return False
        try:
            Path(cd_file).write_text(f"{target_dir.resolve()}\n", encoding="utf-8")
        except OSError as e:
            logging.getLogger("treefort").warning(
                f"Could not write directory change to {cd_file}: {e}"
            )
            return False
        return True

    def current_worktree_name(self) -> str | None:
        """Return the managed worktree name containing the current directory.

        Resolution is pure filesystem work, so it never spawns ``git``. A ``.git`` file only
        counts when its ``gitdir:`` target sits directly under the project's worktree admin
        directory, which rejects submodules, nested clones, and foreign worktrees.

        :return: The POSIX worktree name relative to the project directory, or ``None``
        """
        try:
            project_dir = self.project_dir.resolve()
        except AncestorNotFoundError:
            return None

        bare_dir = (project_dir / ".bare").resolve()
        current_dir = self.cwd.resolve()
        if current_dir == project_dir or current_dir.is_relative_to(bare_dir):
            return None
        if not current_dir.is_relative_to(project_dir):
            return None

        worktrees_admin = (bare_dir / "worktrees").resolve()
        for candidate in (current_dir, *current_dir.parents):
            if candidate == project_dir:
                return None
            gitdir_target = _gitdir_target(candidate)
            if gitdir_target is None or gitdir_target.parent != worktrees_admin:
                continue
            return candidate.relative_to(project_dir).as_posix()
        return None

    def migrate(self):
        if self._migrated:
            return

        project_found = True
        try:
            parent_dir = self.project_dir.parent
        except AncestorNotFoundError:
            parent_dir = self.cwd.parent
            project_found = False

        # migrate global config
        try:
            closest_gh_dir = find_up(".gh", parent_dir)
            old_global_config_dir = closest_gh_dir / "worktree"
            new_global_config_dir = closest_gh_dir.parent / ".treefort"
        except AncestorNotFoundError:
            # No .gh ancestor, fall back to the standard user locations
            home_dir = Path.home()
            old_global_config_dir = home_dir / ".gh" / "worktree"
            new_global_config_dir = home_dir / ".treefort"

        if old_global_config_dir.exists():
            migrate_old_config(old_global_config_dir, new_global_config_dir)

        if project_found:
            # migrate project config
            try:
                old_config_dir = self.project_dir / ".gh" / "worktree"
                new_config_dir = self.project_dir / ".treefort"
                if old_config_dir.exists():
                    migrate_old_config(old_config_dir, new_config_dir)
            except AncestorNotFoundError:
                pass

        self._migrated = True

    def reset_properties(self):
        """Resets any cached properties"""
        self._cached_project_dir = None

    def assert_within_project(self):
        try:
            find_up(".bare", self.cwd)
        except AncestorNotFoundError as e:
            raise ProjectNotFoundError("Project not found") from e

    def get_config(self) -> RepositoryConfig:
        """Get the repository configuration."""
        return self._config_manager.get_config(self.config_dir)

    def get_global_config(self) -> GlobalConfig:
        """Get the global configuration."""
        return self._config_manager.get_global_config(self.global_config_dir)

    def set_config(self, config: ConfigProtocol):
        """Save a configuration to disk."""
        self._config_manager.save_config(config)
