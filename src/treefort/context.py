import logging
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

from treefort.config import (
    ConfigManager,
    ConfigProtocol,
    GlobalConfig,
    RepositoryConfig,
)
from treefort.errors import AncestorNotFoundError, ProjectNotFoundError
from treefort.utils import find_up

logger = logging.getLogger(__name__)

# Environment variable holding the file the parent shell reads directory-change
# directives from; set by the wrapper installed via `treefort shell-init`. The wrapper
# always points this at an absolute path, since a relative path would resolve against
# whatever directory the command has changed into.
CD_FILE_ENV_VAR = "TREEFORT_CD_FILE"


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
    def use(self, cwd: str | Path):
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
                # The previous directory may have been deleted by the command, such as when
                # removing the active worktree; recover in the project root instead, or in
                # the requested directory if the project cannot be resolved anymore.
                try:
                    fallback_dir = self.project_dir
                except AncestorNotFoundError:
                    fallback_dir = cwd_path
                os.chdir(fallback_dir)
                self.cwd = fallback_dir
                if self.shell_integration_active and not self.request_cd(fallback_dir):
                    logger.warning(f'Shell was not moved; run: cd "{fallback_dir}"')

    @property
    def shell_integration_active(self) -> bool:
        """Whether the shell wrapper installed by ``treefort shell-init`` is driving this run."""
        return bool(os.environ.get(CD_FILE_ENV_VAR))

    def request_cd(self, target_dir: Path) -> bool:
        """Request that the parent shell change into the given directory.

        Writes the resolved directory path to the file named by ``TREEFORT_CD_FILE``,
        which the shell wrapper installed by ``treefort shell-init`` reads after the
        command exits. A child process cannot change its parent's directory, so this
        directive file is the only channel to move the user's shell.

        :param target_dir: The directory the shell should change into
        :return: True when the directive was written, False when shell integration is
            inactive or the write failed
        """
        cd_file = os.environ.get(CD_FILE_ENV_VAR)
        if not cd_file:
            return False
        try:
            Path(cd_file).write_text(f"{target_dir.resolve()}\n", encoding="utf-8")
        except OSError as e:
            logger.warning(f"Could not write directory change to {cd_file}: {e}")
            return False
        return True

    def current_worktree_name(self) -> str | None:
        """Return the name of the worktree the current directory is inside, if any.

        The name is the worktree root relative to the project directory, so nested
        worktrees report names like ``subpath/feature``, while subdirectories inside a
        worktree report the worktree they belong to. Returns ``None`` at the project
        root, inside the ``.bare`` repository, or outside the project entirely.

        Directories holding a nested git repository, such as a submodule or a vendored
        clone, are reported as if they were worktree roots; callers that need an exact
        match should validate the name against the project's actual worktrees.

        :return: The worktree name, or ``None`` when not inside a worktree
        """
        try:
            project_dir = self.project_dir.resolve()
        except AncestorNotFoundError:
            return None

        try:
            git_path = find_up(".git", self.cwd)
        except AncestorNotFoundError:
            return None

        worktree_root = git_path.parent
        if worktree_root == project_dir or not worktree_root.is_relative_to(project_dir):
            return None
        return worktree_root.relative_to(project_dir).as_posix()

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
