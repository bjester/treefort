from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import fire

import treefort.cli as cli_module
from treefort.cli import (
    _flag_parameter,
    _is_flag,
    main,
    replace_alias,
    replace_literal_args,
    replace_verbose,
)


class StubCommand:
    """A command that only declares what `replace_literal_args` reads."""

    def __init__(self, name: str, literal_args: tuple[str, ...] = ()):
        self._name = name
        self._literal_args = literal_args


class StubCli:
    """A component with the command registry shape `replace_literal_args` reads."""

    def __init__(self, *commands: StubCommand):
        self._commands = list(commands)


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
    @patch("treefort.cli.replace_literal_args")
    @patch("treefort.cli.replace_alias")
    @patch("treefort.cli.replace_verbose")
    @patch("treefort.cli.WorktreeCommands")
    def test_main__builds_component_replaces_aliases_and_invokes_fire(
        self,
        commands_cls_mock,
        replace_verbose_mock,
        replace_alias_mock,
        replace_literal_args_mock,
        fire_mock,
    ):
        component = Mock()
        commands_cls_mock.return_value = component
        replace_verbose_mock.return_value = False

        main()

        replace_verbose_mock.assert_called_once_with()
        commands_cls_mock.assert_called_once_with(verbose=False)
        replace_alias_mock.assert_called_once_with(component)
        replace_literal_args_mock.assert_called_once_with(component)
        fire_mock.assert_called_once_with(component=component)

    @patch("treefort.cli.fire.Fire")
    @patch("treefort.cli.WorktreeCommands")
    @patch("treefort.cli.sys.argv", ["treefort", "switch", "-"])
    def test_main__rewrites_literal_target_for_switch_subcommand(
        self, commands_cls_mock, fire_mock
    ):
        component = MagicMock()
        component._alias_map = {}
        component._commands = [StubCommand("switch", ("target",))]
        commands_cls_mock.return_value = component

        main()

        self.assertEqual(["treefort", "switch", "--target='-'"], cli_module.sys.argv)
        fire_mock.assert_called_once_with(component=component)

    @patch("treefort.cli.fire.Fire")
    @patch("treefort.cli.WorktreeCommands")
    @patch("treefort.cli.sys.argv", ["treefort", "sw", "-"])
    def test_main__rewrites_literal_target_after_alias_replacement(
        self, commands_cls_mock, fire_mock
    ):
        component = MagicMock()
        component._alias_map = {"sw": "switch"}
        component._commands = [StubCommand("switch", ("target",))]
        commands_cls_mock.return_value = component

        main()

        self.assertEqual(["treefort", "switch", "--target='-'"], cli_module.sys.argv)
        fire_mock.assert_called_once_with(component=component)

    @patch("treefort.cli.fire.Fire")
    @patch("treefort.cli.replace_literal_args")
    @patch("treefort.cli.replace_alias")
    @patch("treefort.cli.replace_verbose")
    @patch("treefort.cli.WorktreeCommands")
    def test_main__reraises_exception_when_runtime_verbose_is_true(
        self,
        commands_cls_mock,
        replace_verbose_mock,
        replace_alias_mock,
        replace_literal_args_mock,
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
    @patch("treefort.cli.replace_literal_args")
    @patch("treefort.cli.replace_alias")
    @patch("treefort.cli.replace_verbose")
    @patch("treefort.cli.WorktreeCommands")
    def test_main__logs_error_and_exits_when_not_verbose(
        self,
        commands_cls_mock,
        replace_verbose_mock,
        replace_alias_mock,
        replace_literal_args_mock,
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


class FireFlagRuleTestCase(TestCase):
    def test_is_flag__matches_fires_own_flag_detection(self):
        # The rewrite only works while this agrees with fire; a fire upgrade that changes its
        # rule must fail here rather than quietly mis-quote arguments.
        for token in (
            "-",
            "--",
            "",
            "-5",
            "-1e5",
            "-.5",
            "-5x",
            "-a",
            "-a=1",
            "-t",
            "-t=1",
            "-target",
            "--target",
            "--target=1.10",
            "---a",
            "-_x",
            "-h",
            "--help",
            "125",
            "1.10",
            "True",
        ):
            with self.subTest(token=token):
                self.assertEqual(_is_flag(token), bool(fire.core._IsFlag(token)))

    def test_flag_parameter__resolves_names_and_unique_shortcuts(self):
        names = ("shell", "cmd")

        self.assertEqual(_flag_parameter("--cmd", names), "cmd")
        self.assertEqual(_flag_parameter("--cmd=125", names), "cmd")
        self.assertEqual(_flag_parameter("-c", names), "cmd")
        self.assertEqual(_flag_parameter("-c=125", names), "cmd")
        self.assertIsNone(_flag_parameter("--shell-name", names))
        self.assertIsNone(_flag_parameter("--yes", names))
        # ambiguous shortcuts and boolean negation are fire features this CLI never uses
        self.assertIsNone(_flag_parameter("-x", names))


class ReplaceLiteralArgsTestCase(TestCase):
    def setUp(self):
        self.switch = StubCommand("switch", ("target",))
        self.shell_init = StubCommand("shell-init", ("shell", "cmd"))
        self.cli = StubCli(self.switch, self.shell_init)

    def rewrite(self, *argv: str) -> list[str]:
        with patch("treefort.cli.sys.argv", ["treefort", *argv]):
            replace_literal_args(self.cli)
            return cli_module.sys.argv

    def test_rewrites_numeric_target_as_a_string_literal(self):
        self.assertEqual(self.rewrite("switch", "125"), ["treefort", "switch", "--target='125'"])

    def test_rewrites_float_like_target_as_a_string_literal(self):
        self.assertEqual(self.rewrite("switch", "1.10"), ["treefort", "switch", "--target='1.10'"])

    def test_rewrites_boolean_like_target_as_a_string_literal(self):
        self.assertEqual(self.rewrite("switch", "True"), ["treefort", "switch", "--target='True'"])

    def test_rewrites_bare_dash_after_switch_subcommand(self):
        self.assertEqual(self.rewrite("switch", "-"), ["treefort", "switch", "--target='-'"])

    def test_escapes_quotes_in_the_target(self):
        self.assertEqual(self.rewrite("switch", "it's"), ["treefort", "switch", '--target="it\'s"'])

    def test_rewrites_target_passed_as_a_separate_flag_value(self):
        self.assertEqual(
            self.rewrite("switch", "--target", "1.10"),
            ["treefort", "switch", "--target", "'1.10'"],
        )

    def test_rewrites_target_passed_as_an_inline_flag_value(self):
        self.assertEqual(
            self.rewrite("switch", "--target=125"),
            ["treefort", "switch", "--target='125'"],
        )

    def test_rewrites_target_passed_with_a_shortcut_flag(self):
        self.assertEqual(
            self.rewrite("switch", "-t", "1.10"), ["treefort", "switch", "-t", "'1.10'"]
        )

    def test_rewrites_inline_shortcut_flag_value(self):
        self.assertEqual(self.rewrite("switch", "-t=1.10"), ["treefort", "switch", "-t='1.10'"])

    def test_rewrites_negative_number_target(self):
        # fire keeps a leading-dash number as a positional argument, not a flag
        self.assertEqual(self.rewrite("switch", "-5"), ["treefort", "switch", "--target='-5'"])

    def test_leaves_a_boolean_target_flag_alone(self):
        # No value follows, so fire treats --target as a boolean; --help must still be a flag
        self.assertEqual(
            self.rewrite("switch", "--target", "--help"),
            ["treefort", "switch", "--target", "--help"],
        )

    def test_rewrites_second_positional_around_an_earlier_flag(self):
        self.assertEqual(
            self.rewrite("shell-init", "--cmd", "mytreefort", "zsh"),
            ["treefort", "shell-init", "--cmd", "'mytreefort'", "--shell='zsh'"],
        )

    def test_rewrites_flag_value_for_the_second_declared_positional(self):
        self.assertEqual(
            self.rewrite("shell-init", "--cmd", "mytreefort"),
            ["treefort", "shell-init", "--cmd", "'mytreefort'"],
        )

    def test_rewrites_inline_flag_value_for_the_second_declared_positional(self):
        self.assertEqual(
            self.rewrite("shell-init", "--cmd=125"),
            ["treefort", "shell-init", "--cmd='125'"],
        )

    def test_rewrites_shortcut_flag_value_for_the_second_declared_positional(self):
        self.assertEqual(
            self.rewrite("shell-init", "-c", "125"),
            ["treefort", "shell-init", "-c", "'125'"],
        )

    def test_rewrites_every_declared_positional(self):
        self.assertEqual(
            self.rewrite("shell-init", "bash", "125"),
            ["treefort", "shell-init", "--shell='bash'", "--cmd='125'"],
        )

    def test_stops_at_the_passthrough_separator(self):
        self.assertEqual(self.rewrite("switch", "--", "125"), ["treefort", "switch", "--", "125"])

    def test_leaves_unrelated_flags_alone(self):
        self.assertEqual(self.rewrite("switch", "--help"), ["treefort", "switch", "--help"])

    def test_noop_for_commands_without_literal_args(self):
        self.cli._commands.append(StubCommand("checkout"))

        self.assertEqual(self.rewrite("checkout", "125"), ["treefort", "checkout", "125"])

    def test_noop_for_unknown_subcommands(self):
        self.assertEqual(self.rewrite("version"), ["treefort", "version"])

    def test_noop_when_target_is_missing(self):
        self.assertEqual(self.rewrite("switch"), ["treefort", "switch"])

    def test_noop_when_no_subcommand_is_given(self):
        self.assertEqual(self.rewrite(), ["treefort"])


class ReplaceLiteralArgsFireTestCase(TestCase):
    def dispatch(self, command: StubCommand, make_callable, argv: list[str]) -> dict:
        """Run fire against a rewritten argv and return the keyword arguments it passed.

        ``make_callable`` receives a list and returns the callable fire dispatches to; it must
        declare the command's real parameters so fire resolves shortcut flags the same way.
        """
        received = []
        cli = StubCli(command)
        setattr(cli, command._name, make_callable(received))

        with patch("treefort.cli.sys.argv", ["treefort", *argv]):
            replace_literal_args(cli)
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                fire.Fire(cli)

        self.assertEqual(len(received), 1, f"fire did not dispatch {argv!r}")
        return received[0]

    def test_rewritten_targets_reach_fire_dispatch_as_text(self):
        def make_callable(received):
            def call(target):
                received.append({"target": target})

            return call

        for argv, expected in (
            (["switch", "125"], "125"),
            (["switch", "1.10"], "1.10"),
            (["switch", "True"], "True"),
            (["switch", "-"], "-"),
            (["switch", "-5"], "-5"),
            (["switch", "--target", "1.10"], "1.10"),
            (["switch", "-t", "1.10"], "1.10"),
            (["switch", "--target=1.10"], "1.10"),
            (["switch", "-t=1.10"], "1.10"),
            (["switch", "it's"], "it's"),
            (["switch", 'say "hi"'], 'say "hi"'),
            (["switch", "back\\slash"], "back\\slash"),
            (["switch", "café"], "café"),
            (["switch", ""], ""),
        ):
            with self.subTest(argv=argv):
                command = StubCommand("switch", ("target",))

                self.assertEqual(self.dispatch(command, make_callable, argv), {"target": expected})

    def test_rewritten_shell_init_arguments_reach_fire_dispatch_as_text(self):
        def make_callable(received):
            def call(shell="bash", cmd="treefort"):
                received.append({"shell": shell, "cmd": cmd})

            return call

        for argv, expected in (
            (["shell-init", "zsh", "125"], {"shell": "zsh", "cmd": "125"}),
            (["shell-init", "zsh"], {"shell": "zsh", "cmd": "treefort"}),
            (["shell-init", "--cmd", "125"], {"shell": "bash", "cmd": "125"}),
            (["shell-init", "--cmd=125", "--shell", "zsh"], {"shell": "zsh", "cmd": "125"}),
            (["shell-init", "-c", "125"], {"shell": "bash", "cmd": "125"}),
            (["shell-init", "--cmd", "mytreefort", "zsh"], {"shell": "zsh", "cmd": "mytreefort"}),
        ):
            with self.subTest(argv=argv):
                command = StubCommand("shell-init", ("shell", "cmd"))

                self.assertEqual(self.dispatch(command, make_callable, argv), expected)


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
