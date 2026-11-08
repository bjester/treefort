from functools import cached_property

from treefort import __version__
from treefort.command import Command
from treefort.commands.checkout import CheckoutCommand
from treefort.commands.create import CreateCommand
from treefort.commands.init import InitCommand
from treefort.commands.install import InstallCommand
from treefort.commands.prune import PruneCommand
from treefort.commands.remove import RemoveCommand
from treefort.errors import AliasConflictError
from treefort.runtime import Runtime


class WorktreeCommands(Command):
    """CLI tool for managing Git worktrees

    Can be used standalone but requires the Github CLI (`gh`) to be installed.
    """

    _name = "treefort"
    _command_classes: tuple[type[Command], ...] = (
        CreateCommand,
        CheckoutCommand,
        InitCommand,
        InstallCommand,
        RemoveCommand,
        PruneCommand,
    )

    def __init__(self, verbose: bool = False):
        """
        :param verbose: Whether to enable logging verbosity
        """
        runtime = Runtime(self._name, verbose)
        super().__init__(runtime)
        self._commands: list[Command] = []
        for command_class in self._command_classes:
            self._add(command_class(self._runtime))

    def _add(self, command: Command):
        """Register a command instance and expose its `__call__` under the canonical name.

        Dispatch invokes the bound `__call__` directly, so parsed positional and flag values
        apply to it without another layer in between.

        :param command: The command instance
        """
        self._commands.append(command)
        setattr(self, command._name, command.__call__)

    @cached_property
    def _alias_map(self) -> dict[str, str]:
        command_names = {command._name for command in self._commands}
        alias_map = {}
        for command in self._commands:
            for alias in command._aliases:
                if alias in command_names:
                    raise AliasConflictError(
                        f"Command {command._name} wants alias {alias} "
                        f"which conflicts with command {alias}"
                    )
                if alias in alias_map:
                    raise AliasConflictError(
                        f"Command {command._name} wants alias {alias} "
                        f"but it already exists for {alias_map[alias]}"
                    )
                alias_map[alias] = command._name
        return alias_map

    def version(self):
        """Outputs the version of the CLI"""
        self._logger.info(f"{self._name} {__version__}")

    def aliases(self):
        """Outputs all of the command aliases"""
        for command in self._commands:
            if len(command._aliases) == 0:
                continue
            self._logger.info(f"{command._name}: {', '.join(command._aliases)}")
