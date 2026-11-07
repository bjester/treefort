from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import treefort.cli as cli_module
from treefort.cli import main, replace_alias, replace_switch_dash, replace_verbose


class ReplaceAliasTestCase(TestCase):
    @patch("treefort.cli.sys.argv", ["treefort", "rm", "feature-branch"])
    def test_rewrites_first_arg_when_alias_exists(self):
        cli = Mock()
        cli._alias_map = {"rm": "remove"}

        replace_alias(cli)

        self.assertEqual("remove", cli._alias_map["rm"])
        self.assertEqual("remove", cli_module.sys.argv[1])

    @patch("treefort.cli.sys.argv", ["treefort", "remove", "feature-branch"])
    def test_noop_when_arg_is_not_alias(self):
        cli = Mock()
        cli._alias_map = {"rm": "remove"}

        replace_alias(cli)

        self.assertEqual("remove", cli_module.sys.argv[1])

    @patch("treefort.cli.sys.argv", ["treefort"])
    def test_noop_when_no_subcommand(self):
        cli = Mock()
        cli._alias_map = {"rm": "remove"}

        replace_alias(cli)

        self.assertEqual(["treefort"], cli_module.sys.argv)


class MainTestCase(TestCase):
    @patch("treefort.cli.fire.Fire")
    @patch("treefort.cli.replace_switch_dash")
    @patch("treefort.cli.replace_alias")
    @patch("treefort.cli.replace_verbose")
    @patch("treefort.cli.WorktreeCommands")
    def test_main__builds_component_replaces_aliases_and_invokes_fire(
        self,
        commands_cls_mock,
        replace_verbose_mock,
        replace_alias_mock,
        replace_switch_dash_mock,
        fire_mock,
    ):
        component = Mock()
        commands_cls_mock.return_value = component
        replace_verbose_mock.return_value = False

        main()

        replace_verbose_mock.assert_called_once_with()
        commands_cls_mock.assert_called_once_with(verbose=False)
        replace_alias_mock.assert_called_once_with(component)
        replace_switch_dash_mock.assert_called_once_with()
        fire_mock.assert_called_once_with(component=component)

    @patch("treefort.cli.fire.Fire")
    @patch("treefort.cli.WorktreeCommands")
    @patch("treefort.cli.sys.argv", ["treefort", "switch", "-"])
    def test_main__rewrites_bare_dash_for_switch_subcommand(self, commands_cls_mock, fire_mock):
        component = MagicMock()
        component._alias_map = {}
        commands_cls_mock.return_value = component

        main()

        self.assertEqual(["treefort", "switch", "--target=-"], cli_module.sys.argv)
        fire_mock.assert_called_once_with(component=component)

    @patch("treefort.cli.fire.Fire")
    @patch("treefort.cli.WorktreeCommands")
    @patch("treefort.cli.sys.argv", ["treefort", "sw", "-"])
    def test_main__rewrites_bare_dash_after_alias_replacement(self, commands_cls_mock, fire_mock):
        component = MagicMock()
        component._alias_map = {"sw": "switch"}
        commands_cls_mock.return_value = component

        main()

        self.assertEqual(["treefort", "switch", "--target=-"], cli_module.sys.argv)
        fire_mock.assert_called_once_with(component=component)

    @patch("treefort.cli.fire.Fire")
    @patch("treefort.cli.replace_switch_dash")
    @patch("treefort.cli.replace_alias")
    @patch("treefort.cli.replace_verbose")
    @patch("treefort.cli.WorktreeCommands")
    def test_main__reraises_exception_when_runtime_verbose_is_true(
        self,
        commands_cls_mock,
        replace_verbose_mock,
        replace_alias_mock,
        replace_switch_dash_mock,
        fire_mock,
    ):
        component = Mock()
        component._runtime.verbose = True
        commands_cls_mock.return_value = component
        replace_verbose_mock.return_value = False
        fire_mock.side_effect = RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            main()

        component._logger.error.assert_not_called()

    @patch("treefort.cli.sys.exit")
    @patch("treefort.cli.fire.Fire")
    @patch("treefort.cli.replace_switch_dash")
    @patch("treefort.cli.replace_alias")
    @patch("treefort.cli.replace_verbose")
    @patch("treefort.cli.WorktreeCommands")
    def test_main__logs_error_and_exits_when_not_verbose(
        self,
        commands_cls_mock,
        replace_verbose_mock,
        replace_alias_mock,
        replace_switch_dash_mock,
        fire_mock,
        exit_mock,
    ):
        component = Mock()
        component._runtime.verbose = False
        commands_cls_mock.return_value = component
        replace_verbose_mock.return_value = False
        fire_mock.side_effect = RuntimeError("boom")

        main()

        component._logger.error.assert_called_once_with("Command failed: boom")
        exit_mock.assert_called_once_with(1)

    @patch("treefort.cli.logging.getLogger")
    @patch("treefort.cli.sys.exit")
    @patch("treefort.cli.replace_verbose")
    @patch("treefort.cli.WorktreeCommands")
    def test_main__logs_error_and_exits_when_component_init_fails(
        self,
        commands_cls_mock,
        replace_verbose_mock,
        exit_mock,
        get_logger_mock,
    ):
        commands_cls_mock._name = "treefort"
        commands_cls_mock.side_effect = RuntimeError("invalid config")
        replace_verbose_mock.return_value = False

        main()

        get_logger_mock.assert_called_once_with("treefort")
        get_logger_mock.return_value.error.assert_called_once_with("Command failed: invalid config")
        exit_mock.assert_called_once_with(1)


class ReplaceSwitchDashTestCase(TestCase):
    @patch("treefort.cli.sys.argv", ["treefort", "switch", "-"])
    def test_rewrites_bare_dash_after_switch_subcommand(self):
        replace_switch_dash()

        self.assertEqual(["treefort", "switch", "--target=-"], cli_module.sys.argv)

    @patch("treefort.cli.sys.argv", ["treefort", "checkout", "-"])
    def test_noop_for_other_subcommands(self):
        replace_switch_dash()

        self.assertEqual(["treefort", "checkout", "-"], cli_module.sys.argv)

    @patch("treefort.cli.sys.argv", ["treefort", "switch", "feature-a"])
    def test_noop_when_target_is_not_dash(self):
        replace_switch_dash()

        self.assertEqual(["treefort", "switch", "feature-a"], cli_module.sys.argv)

    @patch("treefort.cli.sys.argv", ["treefort", "switch"])
    def test_noop_when_target_is_missing(self):
        replace_switch_dash()

        self.assertEqual(["treefort", "switch"], cli_module.sys.argv)


class ReplaceVerboseTestCase(TestCase):
    @patch("treefort.cli.sys.argv", ["treefort", "-v", "remove", "feature"])
    def test_rewrites_args_and_returns_true_for_short_flag(self):
        is_verbose = replace_verbose()

        self.assertTrue(is_verbose)
        self.assertEqual(["treefort", "remove", "feature"], cli_module.sys.argv)

    @patch(
        "treefort.cli.sys.argv",
        ["treefort", "--verbose", "remove", "feature"],
    )
    def test_rewrites_args_and_returns_true_for_long_flag(self):
        is_verbose = replace_verbose()

        self.assertTrue(is_verbose)
        self.assertEqual(["treefort", "remove", "feature"], cli_module.sys.argv)

    @patch(
        "treefort.cli.sys.argv",
        ["treefort", "-v", "--verbose", "remove", "feature"],
    )
    def test_consumes_all_verbose_flags_before_passthrough_separator(self):
        is_verbose = replace_verbose()

        self.assertTrue(is_verbose)
        self.assertEqual(["treefort", "remove", "feature"], cli_module.sys.argv)

    @patch(
        "treefort.cli.sys.argv",
        ["treefort", "remove", "--", "--verbose", "-v", "feature"],
    )
    def test_does_not_strip_verbose_flags_after_passthrough_separator(self):
        is_verbose = replace_verbose()

        self.assertFalse(is_verbose)
        self.assertEqual(
            ["treefort", "remove", "--", "--verbose", "-v", "feature"],
            cli_module.sys.argv,
        )

    @patch("treefort.cli.sys.argv", ["treefort", "remove", "feature"])
    def test_noop_when_no_verbose_flag_is_present(self):
        is_verbose = replace_verbose()

        self.assertFalse(is_verbose)
        self.assertEqual(["treefort", "remove", "feature"], cli_module.sys.argv)
