"""Emit shell integration: a directory-changing wrapper and patched fire tab completion."""

import inspect
import re
from typing import TYPE_CHECKING

from fire import completion as fire_completion

from treefort.command import Command
from treefort.errors import ValidationError
from treefort.hooks import Hook
from treefort.runtime import Runtime
from treefort.shells import SHELL_MODULES, SHELLS

if TYPE_CHECKING:
    from treefort.main import WorktreeCommands

# The first character must be alphanumeric so the generated scripts can always invoke the
# command; re.fullmatch anchors strictly, unlike the "$" in re.match which a trailing
# newline slips past.
CMD_NAME_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9_-]*"


class ShellInitCommand(Command):
    """Emit shell integration for the treefort command"""

    _name = "shell-init"

    def __init__(self, runtime: Runtime, cli: "WorktreeCommands"):
        super().__init__(runtime)
        self._cli = cli

    def __call__(self, shell: str = "bash", cmd: str = "treefort"):
        """
        Emit shell integration for bash, zsh, or fish.

        The output contains a wrapper function that changes the shell's working directory
        after commands that request a directory change (such as `switch`), plus tab
        completion for subcommands, aliases, flags, git remotes, branches, worktrees, and
        hook names. Evaluate it once in your shell profile:

            eval "$(treefort shell-init bash)"

        The fish wrapper requires fish 3.1 or newer.

        :param shell: The shell to generate integration for: bash, zsh, or fish
        :param cmd: The command name the wrapper and completion register for
        :raises ValidationError: When `shell` or `cmd` is not supported
        :raises CompletionTemplateError: When fire's completion template no longer matches
            what the generated script patches
        """
        if shell not in SHELLS:
            raise ValidationError(f"Unsupported shell: {shell}. Choose one of: {', '.join(SHELLS)}")
        if not re.fullmatch(CMD_NAME_PATTERN, cmd):
            raise ValidationError(f"Invalid command name: {cmd}")

        # Build both before printing anything: a template mismatch must not leave a wrapper
        # installed with no completion behind it.
        wrapper = self._wrapper(shell, cmd)
        completion = self._completion_script(shell, cmd)

        print(wrapper)
        print(completion)

    def _wrapper(self, shell: str, cmd: str) -> str:
        """The wrapper function that performs requested directory changes in the shell."""
        return SHELL_MODULES[shell].wrapper(cmd)

    def _completion_script(self, shell: str, cmd: str) -> str:
        """The tab completion script for the given shell."""
        return SHELL_MODULES[shell].completion_script(
            cmd, self._completion_commands(), self._hook_names()
        )

    def _completion_commands(self) -> list[tuple[str, ...]]:
        """Command tuples for fire's completion maps, with aliases and true flags only.

        Parameters without a default must be passed positionally, so their `--flag` form is
        filtered out; keyword parameters stay as `--flag` suggestions. Every tuple is
        duplicated under the command's aliases so aliased invocations complete identically.
        """
        commands: list[tuple[str, ...]] = []
        for command in fire_completion._Commands(self._cli):
            if self._is_positional_flag(command):
                continue
            commands.append(command)
            commands.extend((alias, *command[1:]) for alias in self._aliases_of(command[0]))
        return commands

    def _is_positional_flag(self, command: tuple[str, ...]) -> bool:
        """Whether the tuple's flag names a parameter that has no default value."""
        if len(command) != 2 or not command[1].startswith("-"):
            return False
        member = getattr(self._cli, command[0], None)
        if member is None:
            return False
        try:
            parameters = inspect.signature(member).parameters
        except (TypeError, ValueError):
            return False
        parameter = parameters.get(command[1][2:].replace("-", "_"))
        return parameter is not None and parameter.default is inspect.Parameter.empty

    def _aliases_of(self, subcommand: str) -> list[str]:
        """The aliases that resolve to the given subcommand."""
        return [alias for alias, target in self._cli._alias_map.items() if target == subcommand]

    @staticmethod
    def _hook_names() -> str:
        """Completion words for the first positional argument of the `hook` subcommand."""
        return " ".join(["list", *[hook.name for hook in Hook]])
