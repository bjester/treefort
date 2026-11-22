import hashlib
import os
import stat
from contextlib import contextmanager
from enum import Enum
from logging import Logger
from pathlib import Path

from treefort import ui
from treefort.context import Context
from treefort.errors import HookError, HookExistsError, ValidationError
from treefort.git import TREE_FILE_MODES, GitCLI
from treefort.operator import ConfigOperator
from treefort.subprocess import SubprocessOperator
from treefort.utils import write_bytes_atomically

GIT_HOOKS_DIR = ".treefort/hooks"


class Hook(Enum):
    pre_init = 1
    post_init = 2
    pre_checkout = 3
    post_checkout = 4
    pre_create = 5
    post_create = 6
    pre_remove = 7
    post_remove = 8

    @property
    def git_path(self):
        return f".treefort/hooks/{self.name}"


class HookExists(HookExistsError):
    pass


class Hooks(ConfigOperator, SubprocessOperator):
    """
    Encapsulates operations for managing and executing hooks in the configuration
    system, such as firing hooks, checking their permissions, and adding new hooks.

    This class is designed to integrate with a configuration directory structure,
    allowing for the execution of hooks with appropriate validation steps. Hooks
    are only executed if they're checksum has been registered as allowed.
    """

    def __init__(self, context: Context, logger: Logger):
        super().__init__(context, logger)
        self.dir_name = "hooks"

    def fire(
        self,
        hook: Hook,
        *args,
        skip_project: bool = False,
        bypass_allowlist: bool = False,
    ) -> bool:
        """
        Fire a hook with given arguments.
        :param hook: The hook to fire
        :param args: The arguments to pass to the hook
        :param skip_project: Whether to skip the project-level hooks
        :param bypass_allowlist: Whether to bypass the allowlist check
        :return: Whether the hook was fired
        """
        fired = False

        for hooks_dir in self.iter_config_dirs(skip_project=skip_project):
            hook_file = hooks_dir / hook.name
            if not hook_file.exists():
                continue

            # Ensure the hook file is executable
            hook_file_str = str(hook_file)
            if not os.access(hook_file_str, os.X_OK):
                self.logger.warning(f"Hook {hook_file_str} is not executable. Skipping.")
                continue

            if not self._check_allowed(hook_file, bypass_allowlist=bypass_allowlist):
                self.logger.warning(f"Hook {hook_file_str} is not allowed to run. Skipping.")
                continue

            fired = True
            self.logger.info(ui.step(f"Running hook {ui.bold(hook.name)}"))
            return_status = self.stream_exec(
                [hook_file_str, *[str(arg) for arg in args]], stream_info=True
            )
            if return_status != 0:
                tail = self._format_output_tail()
                detail = f":{tail}" if tail else ""
                raise HookError(f"Hook {hook.name} failed with exit code {return_status}{detail}")
        return fired

    def _check_allowed(self, hook_file: Path, bypass_allowlist: bool = False) -> bool:
        """
        Check if a hook is allowed to run.
        :param hook_file: The path to the hook file
        :param bypass_allowlist: Whether to bypass the allowlist check
        :return: Whether the hook is allowed to run
        """
        with hook_file.open("rb") as f:
            content = f.read()
        checksum = hashlib.sha256(content).hexdigest()

        global_config = self.context.get_global_config()
        hook_file_str = str(hook_file)

        if global_config.allowed_hooks.get(hook_file_str, default="") == checksum:
            return True

        self.logger.warning(f"New/modified hook found: {hook_file_str}")
        if bypass_allowlist:
            self.logger.warning(f"WARNING! Bypassing allowlist check for {hook_file_str}")
            return True

        response = input("Do you want to allow this hook to run? (y/N): ")
        if response.lower() == "y":
            global_config.allow_hook(hook_file_str, checksum)
            self.context.set_config(global_config.allowed_hooks)
            return True

        return False

    @contextmanager
    def add(self, hook: Hook):
        """
        Add a new hook to the worktree configuration and yields a file handle to write its contents.
        :param hook: The hook to add
        """
        hooks_dir = self.context.config_dir / "hooks"
        hook_file = hooks_dir / hook.name
        hooks_dir.mkdir(parents=True, exist_ok=True)

        if hook_file.exists():
            raise HookExists(f"Hook {hook_file} already exists.")

        # copy it to config
        with hook_file.open("w", encoding="utf-8", newline="\n") as f:
            yield f

        # allow exec
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

    def sync_from_git(self, git: GitCLI, ref: str, overwrite: bool = True) -> int:
        """
        Synchronize the project hooks tracked in a Git ref into the configuration directory.
        :param git: The Git CLI used to read the tree entries and blobs
        :param ref: The Git ref to read the hooks from
        :param overwrite: Whether to replace hooks that already exist on disk
        :return: The number of hooks written
        """
        hooks_dir = self.context.config_dir / self.dir_name
        synced = 0

        for entry in git.ls_tree_entries(ref, GIT_HOOKS_DIR):
            if entry.obj_type != "blob" or entry.mode not in TREE_FILE_MODES:
                continue
            if not entry.path.startswith(f"{GIT_HOOKS_DIR}/"):
                continue

            rel_path = entry.path.removeprefix(f"{GIT_HOOKS_DIR}/")
            dest = self._resolve_dest(hooks_dir, rel_path, entry.path)
            if "/" in rel_path or rel_path not in Hook.__members__:
                continue
            if not overwrite and dest.exists():
                self.logger.warning(f"Hook {dest} already exists. Skipping.")
                continue

            write_bytes_atomically(dest, git.read_blob(entry.sha), 0o755)
            synced += 1

        return synced

    def _resolve_dest(self, hooks_dir: Path, rel_path: str, git_path: str) -> Path:
        """
        Resolve a hook entry to its destination, refusing any path that escapes the hooks directory.
        :param hooks_dir: The directory hooks are extracted into
        :param rel_path: The entry path relative to the Git hooks directory
        :param git_path: The full entry path, for diagnostics
        :return: The destination path for the hook
        """
        dest = hooks_dir / rel_path
        if rel_path.startswith("..") or not dest.resolve().is_relative_to(hooks_dir.resolve()):
            raise ValidationError(f"Hook path escapes {hooks_dir}: {git_path}")
        return dest
