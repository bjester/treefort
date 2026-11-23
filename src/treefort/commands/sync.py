from typing import Annotated

from treefort import ui
from treefort.command import Arg, Command, Completion, command


@command("sync")
class SyncCommand(Command):
    """Synchronize project hooks and templates from the repository"""

    def __call__(
        self,
        ref: Annotated[str | None, Arg(complete=Completion.BRANCH_REF)] = None,
        *,
        fetch: bool = True,
    ) -> None:
        """Synchronize project hooks and templates from a Git ref.

        Copies `.treefort/hooks/` and `.treefort/templates/` out of the repository Git tree
        into the project configuration directory. Files are written byte for byte, so binary
        templates survive intact, and executable bits recorded in Git are preserved.

        Modified hooks are not approved automatically; the next hook execution prompts for
        approval of the new checksum.

        Examples:
            treefort sync
            treefort sync upstream/release
            treefort sync --no-fetch

        :param ref: Git ref to sync from (defaults to `<default_remote>/<default_branch>`)
        :param fetch: Fetch before syncing, from the remote the ref names when it names one
        """
        self._context.assert_within_project()
        config = self._context.get_config()
        remote = self._runtime.get_default_remote()
        remote_name = remote.name if remote else "origin"
        target_ref = ref or f"{remote_name}/{config.default_branch}"

        with self._context.use(self._context.project_dir):
            if fetch:
                fetch_remote = self._resolve_fetch_remote(ref, remote_name)
                with ui.spinner(f"Fetching {fetch_remote}...", enabled=self._ui_enabled):
                    self._runtime.git.fetch(fetch_remote)

            hook_count = self._runtime.hooks.sync_from_git(
                self._runtime.git, target_ref, overwrite=True
            )
            template_count = self._runtime.templates.sync_from_git(
                self._runtime.git, target_ref, overwrite=True
            )

            self._logger.info(
                ui.success(
                    f"Synced {hook_count} hook(s) and {template_count} template(s) "
                    f"from {ui.bold(target_ref)}"
                )
            )

    def _resolve_fetch_remote(self, ref: str | None, default_remote: str) -> str:
        """Return the remote to fetch, which is the one an explicit ref names when it has one.

        A ref such as `upstream/release` is read from the remote-tracking namespace, so
        fetching the default remote would leave it stale. A ref that names no configured
        remote, such as `main` or `refs/pull/7/head`, falls back to the default remote.

        :param ref: The ref the caller passed, or ``None`` for the default branch
        :param default_remote: The remote to fetch when the ref names none
        :return: The name of the remote to fetch
        """
        if not ref:
            return default_remote
        prefix = ref.split("/", 1)[0]
        remote_names = {remote.name for remote in self._runtime.git.remote()}
        return prefix if prefix in remote_names else default_remote
