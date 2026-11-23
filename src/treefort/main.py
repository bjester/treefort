from treefort import __version__
from treefort.command import BaseCommand, Command
from treefort.commands.checkout import CheckoutCommand
from treefort.commands.create import CreateCommand
from treefort.commands.init import InitCommand
from treefort.commands.install import InstallCommand
from treefort.commands.prune import PruneCommand
from treefort.commands.remove import RemoveCommand
from treefort.commands.shell_init import ShellInitCommand
from treefort.commands.switch import SwitchCommand
from treefort.commands.sync import SyncCommand
from treefort.runtime import Runtime


class WorktreeCommands(Command):
    """CLI tool for managing Git worktrees

    Can be used standalone but requires the Github CLI (`gh`) to be installed.
    """

    _name = "treefort"
    _command_classes: tuple[type[BaseCommand], ...] = (
        CreateCommand,
        CheckoutCommand,
        InitCommand,
        InstallCommand,
        RemoveCommand,
        PruneCommand,
        ShellInitCommand,
        SwitchCommand,
        SyncCommand,
    )

    def __init__(self, verbose: bool = False):
        """
        :param verbose: Whether to enable logging verbosity
        """
        runtime = Runtime(self._name, verbose)
        super().__init__(runtime)
        self._commands: list[BaseCommand] = []
        for command_class in self._command_classes:
            command = command_class(self._runtime)
            self._commands.append(command)
            setattr(self, command._name, command)

    @classmethod
    def command_class(cls, name: str) -> type[BaseCommand] | None:
        """Return the command class registered under ``name``.

        :param name: The canonical command name
        :return: The registered class, or ``None`` for a built-in method or unknown name
        """
        for command_class in cls._command_classes:
            if command_class._name == name:
                return command_class
        return None

    def version(self):
        """Outputs the version of the CLI"""
        self._logger.info(f"{self._name} {__version__}")

    def aliases(self):
        """Outputs all of the command aliases"""
        for command in self._commands:
            if len(command._aliases) == 0:
                continue
            self._logger.info(f"{command._name}: {', '.join(command._aliases)}")
