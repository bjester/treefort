import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from treefort import ui
from treefort.command import Arg, Command, Completion, command
from treefort.errors import (
    AncestorNotFoundError,
    HookError,
    ProjectNotFoundError,
)
from treefort.hooks import HOOKS, HookRequest

LIST_TABLE_HEADERS = ["HOOK", "SCOPE", "EXECUTABLE", "STATUS", "PATH"]


@command("hook")
class HookCommand(Command):
    """List configured hooks or run a hook on demand"""

    def __call__(
        self,
        hook_name: Annotated[str | None, Arg(complete=Completion.HOOK)] = None,
        worktree_name: Annotated[str | None, Arg(complete=Completion.WORKTREE)] = None,
        *,
        base_ref: Annotated[str | None, Arg(complete=Completion.BASE_REF)] = None,
        yes: Annotated[bool, Arg(short="-y")] = False,
    ) -> None:
        """List configured hooks or run a hook on demand.

        Without a hook name, every global and project lifecycle hook is listed with its scope,
        whether it is executable, and whether its checksum is approved. Naming a hook runs it
        with the same arguments and environment the lifecycle command passes, which lets you
        iterate on a hook without creating or removing a worktree.

        Examples:
            treefort hook
            treefort hook list
            treefort hook post_create my-worktree
            treefort hook pre_remove --yes

        :param hook_name: Hook name to run, or `list` to display configured hooks
        :param worktree_name: Target worktree name for worktree hooks (defaults to current worktree)
        :param base_ref: Override the ref passed as the hook's second argument. Defaults to the
            ref the lifecycle command uses: `<remote>/<branch>` for checkout hooks and
            `<remote>/<default_branch>` for create hooks. A worktree checked out from a fork PR
            needs `--base-ref <fork-remote>/<branch>` to match what `checkout` passed, because
            nothing records which remote the checkout used
        :param yes: Run new or modified hooks without asking for confirmation
        """
        if hook_name is None or hook_name == "list":
            self._list_hooks()
            return

        self._run_hook(hook_name, worktree_name, base_ref=base_ref, yes=yes)

    def _list_hooks(self) -> None:
        """Print every configured lifecycle hook, or report that none are configured."""
        hook_files = [
            (scope, hook_file)
            for scope, hooks_dir in self._iter_hook_dirs()
            for hook_file in sorted(hooks_dir.iterdir())
            if hook_file.is_file() and hook_file.name in HOOKS
        ]

        if not hook_files:
            self._logger.info("No hooks configured.")
            return

        rows = [
            [
                hook_file.name,
                scope,
                "yes" if os.access(hook_file, os.X_OK) else "no",
                self._status(scope, hook_file),
                str(hook_file),
            ]
            for scope, hook_file in hook_files
        ]

        print(ui.format_table(LIST_TABLE_HEADERS, rows))

    def _status(self, scope: str, hook_file: Path) -> str:
        """Report a hook's allowlist status, or note that a project-level hook never runs.

        `pre_init` fires before the project exists, so a project-level copy is listed but cannot
        execute. Reporting it as `global-only` keeps it discoverable without suggesting it will
        run.
        :param scope: The scope name the hook was found under
        :param hook_file: The hook file to report on
        :return: The value for the `STATUS` column
        """
        if scope == "project" and HOOKS[hook_file.name].skip_project:
            return "global-only"
        return str(self._runtime.hooks.approval_status(hook_file))

    def _iter_hook_dirs(self) -> Iterator[tuple[str, Path]]:
        """Yield the existing global and project hook directories with their scope name."""
        candidates = [("global", self._context.global_config_dir / "hooks")]
        try:
            candidates.append(("project", self._context.config_dir / "hooks"))
        except (ProjectNotFoundError, AncestorNotFoundError):
            # Listing works outside a project, where only global hooks can exist
            pass

        for scope, hooks_dir in candidates:
            if hooks_dir.is_dir():
                yield scope, hooks_dir

    def _run_hook(
        self,
        hook_name: str,
        worktree_name: str | None,
        *,
        base_ref: str | None,
        yes: bool,
    ) -> None:
        """Run one lifecycle hook with the arguments and environment its command passes."""
        hook = HOOKS.get(hook_name)
        if hook is None:
            raise HookError(f"Unknown hook '{hook_name}'")

        self._context.assert_within_project()
        request = HookRequest(
            config=self._context.get_config(),
            project_dir=self._context.project_dir,
            resolve_remote=self._default_remote,
            resolve_worktree=self._context.current_worktree_name,
            resolve_clone_uri=self._clone_uri,
            base_ref=base_ref,
            named_worktree=worktree_name,
        )
        # The arguments are resolved before entering the project directory, because the current
        # worktree is derived from where the caller stands
        args = hook.args(request)

        with self._context.use(self._context.project_dir):
            fired = self._runtime.hooks.fire(hook, *args, bypass_allowlist=yes)

        if not fired:
            self._logger.info(f"No {hook.name} hook ran.")

    def _default_remote(self) -> str | None:
        """Return the name of the default remote, or `None` when none matches."""
        remote = self._runtime.get_default_remote()
        return remote.name if remote else None

    def _clone_uri(self) -> str | None:
        """Return the URI the `origin` remote records, which is the one `init` cloned."""
        remote = self._runtime.get_remote(name="origin")
        return remote.uri if remote else None
