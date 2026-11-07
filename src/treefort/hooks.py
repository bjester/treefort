import hashlib
import os
import stat
from contextlib import contextmanager
from enum import Enum
from logging import Logger
from pathlib import Path

from treefort.context import Context
from treefort.errors import HookError, HookExistsError
from treefort.operator import ConfigOperator
from treefort.subprocess import SubprocessOperator


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
            return_status = self.stream_exec([hook_file_str, *[str(arg) for arg in args]])
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
