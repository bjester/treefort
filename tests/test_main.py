from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import treefort.main as main_module
from treefort.errors import AliasConflictError
from treefort.main import WorktreeCommands


class WorktreeCommandsTestCase(TestCase):
    def setUp(self):
        self.commands = WorktreeCommands()

    def test_add__registers_command_name_but_not_alias(self):
        self.assertTrue(callable(self.commands.remove))
        self.assertFalse(hasattr(self.commands, "rm"))

    def test_alias_map__contains_remove_alias_mapping(self):
        alias_map = self.commands._alias_map

        self.assertIn("rm", alias_map)
        self.assertEqual(alias_map["rm"], "remove")

    def test_init__instantiates_every_declared_command_class(self):
        self.assertEqual(
            ["create", "checkout", "init", "install", "remove", "prune"],
            [command._name for command in self.commands._commands],
        )

    def test_alias_map__raises_when_an_alias_conflicts_with_a_command_name(self):
        self.commands._commands.append(SimpleNamespace(_name="duplicate", _aliases=["prune"]))

        with self.assertRaisesRegex(
            AliasConflictError,
            "Command duplicate wants alias prune which conflicts with command prune",
        ):
            _ = self.commands._alias_map

    def test_alias_map__raises_regardless_of_command_registration_order(self):
        self.commands._commands = [
            SimpleNamespace(_name="alpha", _aliases=["prune"]),
            SimpleNamespace(_name="prune", _aliases=[]),
        ]

        with self.assertRaisesRegex(
            AliasConflictError,
            "Command alpha wants alias prune which conflicts with command prune",
        ):
            _ = self.commands._alias_map

    def test_alias_map__raises_when_aliases_conflict(self):
        self.commands._commands.append(SimpleNamespace(_name="duplicate", _aliases=["rm"]))

        with self.assertRaisesRegex(
            AliasConflictError,
            "Command duplicate wants alias rm but it already exists for remove",
        ):
            _ = self.commands._alias_map

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
