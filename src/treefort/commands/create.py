from typing import Annotated

from treefort import ui
from treefort.command import Arg, Command, Completion, command
from treefort.hooks import Hook
from treefort.utils import normalize_worktree_name


@command("create")
class CreateCommand(Command):
    def __call__(
        self,
        worktree_name: str,
        base_ref: Annotated[str | None, Arg(complete=Completion.BASE_REF)] = None,
        *,
        yes: Annotated[bool, Arg(short="-y")] = False,
    ):
        """
        Create a new worktree in the current project.

        This will add a new directory to your project directory with the name provided to
        `worktree_name`. By default, the worktree branch will be based off the default GitHub
        repository branch. To base it off another branch, you may use specify it as the second
        argument. If the branch you want to base off contains a slash (`branch/name`), you must also
        specify prefix it with the remote name and a slash, e.g. `remote/branch/name`.

        Examples:
            treefort create testing-create
            treefort create testing-create main
            treefort create testing-create upstream/main
            treefort create testing-create upstream/some/other/branch

        :param worktree_name: The name of the worktree
        :param base_ref: The base reference to create the worktree from
        :param yes: Perform any action automatically without asking (execute new or modified hooks)
        :type yes: bool
        """
        self._context.assert_within_project()
        git_remote_name = None

        if base_ref is None:
            project_config = self._context.get_config()
            base_ref = project_config.default_branch
        elif "/" in base_ref:
            git_remote_name, base_ref = base_ref.split("/", 1)

        if git_remote_name is None:
            git_remote = self._runtime.get_default_remote()
            git_remote_name = git_remote.name

        with ui.spinner(f"Fetching {git_remote_name}...", enabled=self._ui_enabled):
            self._runtime.git.fetch(git_remote_name)

        normalized_name = normalize_worktree_name(worktree_name)

        with self._context.use(self._context.project_dir):
            self._runtime.hooks.fire(
                Hook.pre_create,
                worktree_name,
                f"{git_remote_name}/{base_ref}",
                normalized_name,
                bypass_allowlist=yes,
            )
            self._runtime.git.add_worktree(worktree_name, f"{git_remote_name}/{base_ref}")
            self._runtime.templates.copy(worktree_name)
            self._runtime.hooks.fire(
                Hook.post_create,
                worktree_name,
                f"{git_remote_name}/{base_ref}",
                normalized_name,
                bypass_allowlist=yes,
            )

            self._logger.info(
                ui.success(
                    f"Created worktree {ui.bold(worktree_name)} (from {ui.dim(base_ref)}) "
                    f"at {ui.dim(str(self._context.project_dir / worktree_name))}"
                )
            )
