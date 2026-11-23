import hashlib
import os
import stat
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from logging import Logger
from pathlib import Path

from treefort import ui
from treefort.config import RepositoryConfig
from treefort.context import Context
from treefort.errors import HookError, HookExistsError, WorktreeNotFoundError
from treefort.git import TREE_FILE_MODES, GitCLI, GitUri
from treefort.operator import ConfigOperator
from treefort.subprocess import SubprocessOperator
from treefort.utils import normalize_worktree_name, resolve_contained, write_bytes_atomically

GIT_HOOKS_DIR = ".treefort/hooks"

# A hook is a user-authored script running with the user's own privileges, so withholding a
# variable is not a security boundary. It keeps the contract honest instead: a hook sees the
# variables treefort exports for it plus what a shell script needs to find its tools and reach
# the network, never a stale ``TREEFORT_*`` value that an outer hook or a hand-run export left
# in the caller.
HOOK_ENV_PASSTHROUGH = frozenset(
    {
        "HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "NO_PROXY",
        "PATH",
        "SHELL",
        "TERM",
        "TMPDIR",
        "USER",
        "XDG_CONFIG_HOME",
    }
)


def _default_branch(config: RepositoryConfig) -> str:
    """Return the configured default branch, tolerating a stub config without one.
    :param config: The repository configuration
    :return: The default branch name
    """
    return getattr(config, "default_branch", "main") or "main"


class ApprovalStatus(str, Enum):
    """Allowlist state of a hook file, as reported by :meth:`Hooks.approval_status`."""

    APPROVED = "approved"
    MODIFIED = "modified"
    UNAPPROVED = "unapproved"

    def __str__(self) -> str:
        """Return the status name, so formatting never depends on the Enum mixin."""
        return self.value


@dataclass(frozen=True, slots=True)
class HookRequest:
    """The inputs a hook needs to build its positional arguments.

    Worktree and remote resolution are methods rather than fields because each reads the
    filesystem or shells out to git, and a hook that does not need them must not pay for them.
    """

    config: RepositoryConfig
    project_dir: Path
    resolve_remote: Callable[[], str | None]
    resolve_worktree: Callable[[], str | None]
    resolve_clone_uri: Callable[[], str | None]
    base_ref: str | None = None
    named_worktree: str | None = None

    def worktree_name(self) -> str:
        """Return the target worktree, resolving the current one when none was named.

        Call this before entering the project directory, because the current worktree is derived
        from the caller's working directory.
        :return: The project-relative worktree name
        """
        worktree_name = self.named_worktree or self.resolve_worktree()
        if worktree_name is None:
            raise WorktreeNotFoundError(
                "Not inside a worktree; pass a worktree name to run this hook"
            )
        return worktree_name

    def default_remote_name(self) -> str:
        """Return the resolved default remote name, falling back to ``origin``."""
        return self.resolve_remote() or "origin"

    def clone_uri(self) -> str:
        """Return the URI ``init`` cloned, falling back to the configured URL.

        ``init`` passes the normalized clone URI, which the ``origin`` remote records, so a hook
        run through ``treefort hook`` receives the same ``$1`` the lifecycle command passes.
        :return: The clone URI, or the configured URL when no remote records one
        """
        return self.resolve_clone_uri() or getattr(self.config, "url", "") or ""

    @property
    def default_branch(self) -> str:
        """Return the configured default branch, tolerating a config stub without one."""
        return _default_branch(self.config)


class BaseHook(ABC):
    """A named lifecycle hook script.

    Subclasses own the parts that vary between hooks: the positional arguments the hook receives,
    the ``TREEFORT_*`` variables it exports, and whether project-level hooks are eligible. Callers
    treat every hook the same way, so no caller classifies a hook by name.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    @property
    def skip_project(self) -> bool:
        """Whether project-level hook directories are excluded."""
        return False

    @abstractmethod
    def build_env(self, context: Context, *args: object) -> dict[str, str]:
        """Return the ``TREEFORT_*`` environment exported to this hook.

        Positional arguments stay the hook's calling convention, so every value is derived from
        the arguments the hook already receives. Each slot is length-guarded because a caller may
        fire a hook with fewer arguments than the lifecycle command passes.
        :param context: The project context
        :param args: The positional arguments the hook receives
        :return: The environment variables to merge over the inherited environment
        """

    @abstractmethod
    def args(self, request: HookRequest) -> tuple[str, ...]:
        """Return the positional arguments this hook receives.
        :param request: The resolved inputs for this invocation
        :return: The positional arguments
        """

    def _repo_env(self, context: Context) -> dict[str, str]:
        """Return the variables every hook that runs with a project present exports.
        :param context: The project context
        :return: The environment variables shared by repository and worktree hooks
        """
        config = context.get_config()
        return {
            "TREEFORT_HOOK": self.name,
            "TREEFORT_REPO_NAME": getattr(config, "name", "") or "",
            "TREEFORT_REPO_DIR": str(context.project_dir),
            "TREEFORT_DEFAULT_BRANCH": _default_branch(config),
        }


class InitHook(BaseHook):
    """A hook that runs around initialization; its arguments describe the repository."""

    __slots__ = ()

    def args(self, request: HookRequest) -> tuple[str, ...]:
        return request.clone_uri(), request.project_dir


class PreInitHook(InitHook):
    """Runs before the clone exists, so it reads nothing from the project context."""

    __slots__ = ()

    @property
    def skip_project(self) -> bool:
        return True

    def build_env(self, context: Context, *args: object) -> dict[str, str]:
        uri = str(args[0]) if len(args) > 0 else ""
        return {
            "TREEFORT_HOOK": self.name,
            "TREEFORT_REPO_NAME": GitUri(uri).name if uri else "",
            "TREEFORT_REPO_DIR": str(args[1]) if len(args) > 1 else "",
        }


class PostInitHook(InitHook):
    """Runs after the clone exists, so it reads the project configuration."""

    __slots__ = ()

    def build_env(self, context: Context, *args: object) -> dict[str, str]:
        return self._repo_env(context)


class WorktreeHook(BaseHook):
    """A hook that runs around a worktree; its arguments describe the worktree.

    This is also the class of the remove hooks, whose second argument is the normalized name
    rather than a ref.
    """

    __slots__ = ()

    def args(self, request: HookRequest) -> tuple[str, ...]:
        worktree_name = request.worktree_name()
        return worktree_name, normalize_worktree_name(worktree_name)

    def build_env(self, context: Context, *args: object) -> dict[str, str]:
        return self._worktree_env(
            context,
            worktree_name=str(args[0]) if len(args) > 0 else "",
            normalized_name=str(args[1]) if len(args) > 1 else "",
        )

    def _worktree_env(
        self, context: Context, worktree_name: str, normalized_name: str
    ) -> dict[str, str]:
        """Return the repository variables plus the worktree variables.
        :param context: The project context
        :param worktree_name: The project-relative worktree name
        :param normalized_name: The normalized worktree name
        :return: The environment variables exported to a worktree hook
        """
        env = self._repo_env(context)
        env["TREEFORT_WORKTREE_NAME"] = worktree_name
        env["TREEFORT_WORKTREE_NAME_NORMALIZED"] = normalized_name
        env["TREEFORT_WORKTREE_DIR"] = (
            str(context.project_dir / worktree_name) if worktree_name else ""
        )
        return env


class BaseRefHook(WorktreeHook):
    """A worktree hook whose second argument is the ref the worktree came from."""

    __slots__ = ()

    def args(self, request: HookRequest) -> tuple[str, ...]:
        worktree_name = request.worktree_name()
        ref = request.base_ref or self.default_ref(request, worktree_name)
        return worktree_name, ref, normalize_worktree_name(worktree_name)

    def build_env(self, context: Context, *args: object) -> dict[str, str]:
        env = self._worktree_env(
            context,
            worktree_name=str(args[0]) if len(args) > 0 else "",
            normalized_name=str(args[2]) if len(args) > 2 else "",
        )
        env["TREEFORT_BASE_REF"] = str(args[1]) if len(args) > 1 else ""
        return env

    @abstractmethod
    def default_ref(self, request: HookRequest, worktree_name: str) -> str:
        """Return the ref the lifecycle command uses when the caller names none.
        :param request: The resolved inputs for this invocation
        :param worktree_name: The target worktree name
        :return: The default ref
        """


class CreateHook(BaseRefHook):
    """Branches a new worktree, so the ref is the base the branch was cut from."""

    __slots__ = ()

    def default_ref(self, request: HookRequest, worktree_name: str) -> str:
        return f"{request.default_remote_name()}/{request.default_branch}"


class CheckoutHook(BaseRefHook):
    """Checks out an existing branch, so the ref is the branch itself."""

    __slots__ = ()

    def default_ref(self, request: HookRequest, worktree_name: str) -> str:
        return f"{request.default_remote_name()}/{worktree_name}"


PRE_INIT = PreInitHook("pre_init")
POST_INIT = PostInitHook("post_init")
PRE_CHECKOUT = CheckoutHook("pre_checkout")
POST_CHECKOUT = CheckoutHook("post_checkout")
PRE_CREATE = CreateHook("pre_create")
POST_CREATE = CreateHook("post_create")
PRE_REMOVE = WorktreeHook("pre_remove")
POST_REMOVE = WorktreeHook("post_remove")

HOOKS: dict[str, BaseHook] = {
    hook.name: hook
    for hook in (
        PRE_INIT,
        POST_INIT,
        PRE_CHECKOUT,
        POST_CHECKOUT,
        PRE_CREATE,
        POST_CREATE,
        PRE_REMOVE,
        POST_REMOVE,
    )
}


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
        hook: BaseHook,
        *args,
        bypass_allowlist: bool = False,
        no_hooks: bool = False,
        env: dict[str, str] | None = None,
    ) -> bool:
        """
        Fire a hook with given arguments.
        :param hook: The hook to fire
        :param args: The arguments to pass to the hook
        :param bypass_allowlist: Whether to bypass the allowlist check
        :param no_hooks: Whether to skip every hook without prompting or running anything
        :param env: Additional environment variables merged over the built hook environment,
            winning on conflict
        :return: Whether the hook was fired
        """
        if no_hooks:
            self.logger.debug(f"Skipping hook {hook.name} (--no-hooks)")
            return False

        fired = False

        for hooks_dir in self.iter_config_dirs(skip_project=hook.skip_project):
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
                [hook_file_str, *[str(arg) for arg in args]],
                env={
                    **self._passthrough_env(),
                    **hook.build_env(self.context, *args),
                    **(env or {}),
                },
                stream_info=True,
                inherit_env=False,
            )
            if return_status != 0:
                tail = self._format_output_tail()
                detail = f":{tail}" if tail else ""
                raise HookError(f"Hook {hook.name} failed with exit code {return_status}{detail}")
        return fired

    @staticmethod
    def _passthrough_env() -> dict[str, str]:
        """Return the standard variables a hook inherits from the caller's environment.

        Everything else is withheld, so a hook cannot observe a ``TREEFORT_*`` value that
        belongs to a different hook or to an outer invocation.
        :return: The inherited variables present in the current environment
        """
        return {name: os.environ[name] for name in HOOK_ENV_PASSTHROUGH if name in os.environ}

    def approval_status(self, hook_file: Path) -> ApprovalStatus:
        """
        Report whether a hook's current checksum is approved, modified, or unrecorded.

        This is the single definition of the allowlist rule. :meth:`_check_allowed` gates
        execution on it and ``treefort hook list`` reports it, so the two cannot disagree.
        :param hook_file: The hook file to check
        :return: `ApprovalStatus.APPROVED` when the recorded checksum matches the file,
            `ApprovalStatus.MODIFIED` when a different checksum is recorded, or
            `ApprovalStatus.UNAPPROVED` when none is recorded
        """
        recorded_checksum = self.context.get_global_config().allowed_hooks.get(
            str(hook_file), default=""
        )
        if not recorded_checksum:
            return ApprovalStatus.UNAPPROVED
        if recorded_checksum == hashlib.sha256(hook_file.read_bytes()).hexdigest():
            return ApprovalStatus.APPROVED
        return ApprovalStatus.MODIFIED

    def _check_allowed(self, hook_file: Path, bypass_allowlist: bool = False) -> bool:
        """
        Check if a hook is allowed to run.
        :param hook_file: The path to the hook file
        :param bypass_allowlist: Whether to bypass the allowlist check
        :return: Whether the hook is allowed to run
        """
        global_config = self.context.get_global_config()
        hook_file_str = str(hook_file)

        if self.approval_status(hook_file) is ApprovalStatus.APPROVED:
            return True

        self.logger.warning(f"New/modified hook found: {hook_file_str}")
        if bypass_allowlist:
            self.logger.warning(f"WARNING! Bypassing allowlist check for {hook_file_str}")
            return True

        response = input("Do you want to allow this hook to run? (y/N): ")
        if response.lower() == "y":
            checksum = hashlib.sha256(hook_file.read_bytes()).hexdigest()
            global_config.allow_hook(hook_file_str, checksum)
            self.context.set_config(global_config.allowed_hooks)
            return True

        return False

    @contextmanager
    def add(self, hook: BaseHook):
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
            dest = resolve_contained(hooks_dir, rel_path)
            if "/" in rel_path or rel_path not in HOOKS:
                continue
            if HOOKS[rel_path].skip_project:
                self.logger.warning(
                    f"Hook {rel_path} is global-only; the synced project copy will never run."
                )
            if not overwrite and dest.exists():
                self.logger.warning(f"Hook {dest} already exists. Skipping.")
                continue

            write_bytes_atomically(dest, git.read_blob(entry.sha), 0o755)
            synced += 1

        return synced
