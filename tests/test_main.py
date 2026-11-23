from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import treefort.main as main_module
from treefort.commands.shell_init import ShellInitCommand
from treefort.commands.switch import SwitchCommand
from treefort.main import WorktreeCommands


class WorktreeCommandsTestCase(TestCase):
    def setUp(self):
        self.commands = WorktreeCommands()

    def test_init__registers_command_name_but_not_alias(self):
        self.assertTrue(callable(self.commands.remove))
        self.assertFalse(hasattr(self.commands, "rm"))

    def test_init__instantiates_every_declared_command_class(self):
        self.assertEqual(
            [
                "create",
                "checkout",
                "init",
                "hook",
                "install",
                "remove",
                "prune",
                "shell-init",
                "switch",
                "sync",
            ],
            [command._name for command in self.commands._commands],
        )

    def test_command_class__returns_the_registered_class(self):
        self.assertIs(SwitchCommand, WorktreeCommands.command_class("switch"))
        self.assertIs(ShellInitCommand, WorktreeCommands.command_class("shell-init"))

    def test_command_class__returns_none_for_a_builtin_method(self):
        for name in ("version", "aliases"):
            with self.subTest(name=name):
                self.assertIsNone(WorktreeCommands.command_class(name))

    def test_command_class__returns_none_for_an_unknown_name(self):
        self.assertIsNone(WorktreeCommands.command_class("bogus"))

    @patch.object(WorktreeCommands, "_logger", new_callable=lambda: Mock())
    def test_version__prints_cli_name_and_version(self, mock_logger):
        self.commands._logger = mock_logger
        self.commands.version()

        mock_logger.info.assert_called_once_with(f"{self.commands._name} {main_module.__version__}")

    @patch.object(WorktreeCommands, "_logger", new_callable=lambda: Mock())
    def test_aliases__prints_only_commands_with_aliases(self, mock_logger):
        self.commands._logger = mock_logger
        self.commands._commands = [
            SimpleNamespace(_name="create", _aliases=[]),
            SimpleNamespace(_name="remove", _aliases=["rm"]),
        ]

        self.commands.aliases()

        mock_logger.info.assert_called_once_with("remove: rm")
