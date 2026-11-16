from unittest import TestCase

from treefort.cli_manifest import CLIManifest, CommandSpec, ParamSpec
from treefort.cli_parser import parse_cli_args
from treefort.errors import CLIUsageError


def param(**overrides) -> ParamSpec:
    values = {
        "name": "name",
        "cli_name": "NAME",
        "kind": "positional",
        "value_type": "str",
        "nullable": False,
        "required": True,
        "has_default": False,
        "default": None,
        "choices": (),
        "short": None,
        "completion": "none",
        "description": "",
    }
    values.update(overrides)
    return ParamSpec(**values)


CREATE = CommandSpec(
    name="create",
    aliases=(),
    summary="Create a new worktree",
    description="",
    params=(
        param(name="worktree_name", cli_name="WORKTREE_NAME"),
        param(
            name="base_ref",
            cli_name="BASE_REF",
            nullable=True,
            required=False,
            has_default=True,
        ),
        param(
            name="yes",
            cli_name="--yes",
            kind="flag",
            value_type="bool",
            required=False,
            has_default=True,
            default=False,
            short="-y",
        ),
    ),
    help_text="create help",
)

REMOVE = CommandSpec(
    name="remove",
    aliases=("rm",),
    summary="Remove a worktree",
    description="",
    params=(
        param(name="worktree_name", cli_name="WORKTREE_NAME"),
        param(
            name="force",
            cli_name="--force",
            kind="flag",
            value_type="bool",
            required=False,
            has_default=True,
            default=False,
            short="-f",
        ),
        param(
            name="delete_branch",
            cli_name="--delete-branch",
            kind="flag",
            value_type="bool",
            required=False,
            has_default=True,
            default=False,
            short="-d",
        ),
    ),
    help_text="remove help",
)

SAMPLE = CommandSpec(
    name="sample",
    aliases=(),
    summary="Sample command",
    description="",
    params=(
        param(name="target", cli_name="TARGET", choices=("bash", "zsh", "fish")),
        param(
            name="extras",
            cli_name="EXTRAS",
            kind="variadic_positional",
            required=False,
        ),
        param(
            name="fetch",
            cli_name="--fetch",
            kind="flag",
            value_type="bool",
            required=False,
            has_default=True,
            default=True,
            short="-F",
        ),
        param(
            name="cmd",
            cli_name="--cmd",
            kind="flag",
            required=False,
            has_default=True,
            default="treefort",
            short="-c",
        ),
        param(
            name="count",
            cli_name="--count",
            kind="flag",
            value_type="int",
            required=False,
            has_default=True,
            default=1,
        ),
        param(
            name="shell",
            cli_name="--shell",
            kind="flag",
            required=False,
            has_default=True,
            default="bash",
            choices=("bash", "zsh"),
        ),
    ),
    help_text="sample help",
)

MANIFEST = CLIManifest(
    prog_name="treefort",
    summary="CLI tool for managing Git worktrees",
    description="",
    commands={"create": CREATE, "remove": REMOVE, "sample": SAMPLE},
    alias_map={"rm": "remove"},
    root_help_text="root help",
    shell_completions={},
)


class ParseCliArgsTestCase(TestCase):
    def parse(self, *argv):
        return parse_cli_args(MANIFEST, list(argv))

    # --- root dispatch and help -------------------------------------------------------------

    def test_returns_root_help_when_there_are_no_arguments(self):
        parsed = self.parse()

        self.assertIsNone(parsed.command_name)
        self.assertTrue(parsed.help_requested)
        self.assertEqual((), parsed.args)

    def test_returns_root_help_when_only_global_flags_are_given(self):
        parsed = self.parse("-v")

        self.assertIsNone(parsed.command_name)
        self.assertTrue(parsed.help_requested)
        self.assertTrue(parsed.verbose)

    def test_returns_command_help_for_the_long_help_flag(self):
        parsed = self.parse("create", "--help")

        self.assertEqual("create", parsed.command_name)
        self.assertTrue(parsed.help_requested)
        self.assertEqual((), parsed.args)

    def test_returns_command_help_for_the_short_help_flag_before_the_command(self):
        parsed = self.parse("-h", "create")

        self.assertEqual("create", parsed.command_name)
        self.assertTrue(parsed.help_requested)

    def test_returns_command_help_for_an_alias(self):
        parsed = self.parse("rm", "-h")

        self.assertEqual("remove", parsed.command_name)
        self.assertTrue(parsed.help_requested)

    def test_returns_command_help_through_the_help_subcommand(self):
        parsed = self.parse("help", "rm")

        self.assertEqual("remove", parsed.command_name)
        self.assertTrue(parsed.help_requested)

    def test_returns_root_help_for_the_bare_help_subcommand(self):
        parsed = self.parse("help")

        self.assertIsNone(parsed.command_name)
        self.assertTrue(parsed.help_requested)

    def test_does_not_validate_required_arguments_when_help_is_requested(self):
        parsed = self.parse("create", "-h")

        self.assertTrue(parsed.help_requested)
        self.assertEqual((), parsed.args)

    # --- usage errors ----------------------------------------------------------------------

    def test_raises_for_an_unknown_command(self):
        with self.assertRaisesRegex(CLIUsageError, "Unknown command 'bogus'.") as context:
            self.parse("bogus")

        self.assertIsNone(context.exception.command_name)

    def test_raises_for_an_unknown_command_even_with_the_help_flag(self):
        with self.assertRaisesRegex(CLIUsageError, "Unknown command 'bogus'."):
            self.parse("bogus", "-h")

    def test_raises_for_an_unknown_help_target(self):
        with self.assertRaisesRegex(CLIUsageError, "Unknown command 'bogus'."):
            self.parse("help", "bogus")

    def test_raises_for_an_unknown_global_flag(self):
        with self.assertRaisesRegex(CLIUsageError, "Unknown flag: --nope"):
            self.parse("--nope")

    def test_raises_when_only_a_passthrough_separator_is_given(self):
        with self.assertRaisesRegex(CLIUsageError, "Missing command."):
            self.parse("--", "extra")

    # --- verbose --------------------------------------------------------------------------

    def test_extracts_verbose_before_the_command(self):
        parsed = self.parse("-v", "create", "feature")

        self.assertTrue(parsed.verbose)
        self.assertEqual(("feature",), parsed.args)

    def test_extracts_verbose_after_the_command(self):
        parsed = self.parse("create", "feature", "--verbose")

        self.assertTrue(parsed.verbose)
        self.assertEqual(("feature",), parsed.args)

    def test_does_not_extract_verbose_after_the_passthrough_separator(self):
        parsed = self.parse("create", "feature", "--", "-v")

        self.assertFalse(parsed.verbose)
        self.assertEqual(("feature", "-v"), parsed.args)

    def test_raises_when_a_value_flag_is_followed_by_verbose(self):
        # Removing the flag first would bind "extra" to --cmd and hide the missing value
        with self.assertRaisesRegex(CLIUsageError, "Flag --cmd requires a value") as context:
            self.parse("sample", "bash", "--cmd", "-v", "extra")

        self.assertEqual("sample", context.exception.command_name)

    def test_raises_for_a_short_value_flag_followed_by_verbose(self):
        with self.assertRaisesRegex(CLIUsageError, "Flag --cmd requires a value"):
            self.parse("sample", "bash", "-c", "-v", "extra")

    def test_help_still_wins_over_a_missing_flag_value(self):
        parsed = self.parse("sample", "--cmd", "-h")

        self.assertTrue(parsed.help_requested)

    def test_extracts_verbose_after_the_help_subcommand(self):
        parsed = self.parse("help", "sample", "-v")

        self.assertEqual("sample", parsed.command_name)
        self.assertTrue(parsed.help_requested)
        self.assertTrue(parsed.verbose)

    def test_reports_verbose_when_help_is_requested(self):
        parsed = self.parse("create", "-v", "-h")

        self.assertTrue(parsed.help_requested)
        self.assertTrue(parsed.verbose)

    # --- aliases and positionals ----------------------------------------------------------

    def test_resolves_an_alias_to_the_canonical_command(self):
        parsed = self.parse("rm", "feature")

        self.assertEqual("remove", parsed.command_name)
        self.assertEqual(("feature",), parsed.args)

    def test_binds_a_required_positional(self):
        parsed = self.parse("create", "feature")

        self.assertEqual(("feature",), parsed.args)
        self.assertEqual({}, parsed.kwargs)

    def test_binds_an_optional_positional(self):
        parsed = self.parse("create", "feature", "release-x")

        self.assertEqual(("feature", "release-x"), parsed.args)

    def test_binds_variadic_positionals(self):
        parsed = self.parse("sample", "bash", "one", "two")

        self.assertEqual(("bash", "one", "two"), parsed.args)

    def test_raises_when_a_required_positional_is_missing(self):
        with self.assertRaisesRegex(
            CLIUsageError, "Missing required argument: WORKTREE_NAME"
        ) as ctx:
            self.parse("create")

        self.assertEqual("create", ctx.exception.command_name)

    def test_raises_for_an_extra_positional(self):
        with self.assertRaisesRegex(CLIUsageError, "Unexpected argument: extra"):
            self.parse("create", "feature", "main", "extra")

    def test_validates_literal_choices(self):
        with self.assertRaisesRegex(CLIUsageError, "Invalid value for TARGET: 'fishx'") as context:
            self.parse("sample", "fishx")

        self.assertEqual("sample", context.exception.command_name)

    def test_accepts_a_literal_choice(self):
        parsed = self.parse("sample", "zsh")

        self.assertEqual(("zsh",), parsed.args)

    def test_keeps_numeric_and_boolean_looking_strings_verbatim(self):
        for value in ("125", "1.10", "True", "0x1f"):
            with self.subTest(value=value):
                parsed = self.parse("create", value)
                self.assertEqual((value,), parsed.args)
                self.assertIsInstance(parsed.args[0], str)

    def test_keeps_numeric_and_boolean_looking_flag_values_verbatim(self):
        for value in ("125", "1.10", "True", "0x1f", "-"):
            with self.subTest(value=value):
                parsed = self.parse("sample", "bash", "--cmd", value)

                self.assertEqual({"cmd": value}, parsed.kwargs)
                self.assertIsInstance(parsed.kwargs["cmd"], str)

    def test_keeps_a_bare_dash_as_a_positional(self):
        parsed = self.parse("create", "-")

        self.assertEqual(("-",), parsed.args)

    def test_appends_passthrough_tokens_to_positionals(self):
        parsed = self.parse("sample", "bash", "--", "-not-a-flag", "two")

        self.assertEqual(("bash", "-not-a-flag", "two"), parsed.args)

    # --- flags ----------------------------------------------------------------------------

    def test_parses_a_long_flag_with_a_separate_value(self):
        parsed = self.parse("sample", "bash", "--cmd", "mytf")

        self.assertEqual({"cmd": "mytf"}, parsed.kwargs)

    def test_parses_a_long_flag_with_an_inline_value(self):
        parsed = self.parse("sample", "bash", "--cmd=mytf")

        self.assertEqual({"cmd": "mytf"}, parsed.kwargs)

    def test_parses_a_short_flag_with_a_separate_value(self):
        parsed = self.parse("sample", "bash", "-c", "mytf")

        self.assertEqual({"cmd": "mytf"}, parsed.kwargs)

    def test_parses_a_short_flag_with_an_inline_value(self):
        parsed = self.parse("sample", "bash", "-c=mytf")

        self.assertEqual({"cmd": "mytf"}, parsed.kwargs)

    def test_accepts_a_snake_case_flag_name(self):
        parsed = self.parse("rm", "feature", "--delete_branch")

        self.assertEqual({"delete_branch": True}, parsed.kwargs)

    def test_parses_a_boolean_flag(self):
        parsed = self.parse("rm", "feature", "--force")

        self.assertEqual({"force": True}, parsed.kwargs)

    def test_parses_a_short_boolean_flag(self):
        parsed = self.parse("rm", "feature", "-f")

        self.assertEqual({"force": True}, parsed.kwargs)

    def test_parses_a_negated_boolean_flag(self):
        parsed = self.parse("sample", "bash", "--no-fetch")

        self.assertEqual({"fetch": False}, parsed.kwargs)

    def test_parses_a_negated_boolean_flag_in_snake_case(self):
        for token in ("--no_delete_branch", "--no-delete-branch"):
            with self.subTest(token=token):
                parsed = self.parse("rm", "feature", token)
                self.assertEqual({"delete_branch": False}, parsed.kwargs)

    def test_parses_an_inline_true_boolean_value(self):
        for value in ("true", "TRUE", "1", "yes"):
            with self.subTest(value=value):
                parsed = self.parse("sample", "bash", f"--fetch={value}")
                self.assertIs(True, parsed.kwargs["fetch"])

    def test_parses_an_inline_false_boolean_value(self):
        for value in ("false", "FALSE", "0", "no"):
            with self.subTest(value=value):
                parsed = self.parse("sample", "bash", f"--fetch={value}")
                self.assertIs(False, parsed.kwargs["fetch"])

    def test_raises_for_an_invalid_boolean_value(self):
        with self.assertRaisesRegex(CLIUsageError, "Invalid value for --fetch: 'maybe'"):
            self.parse("sample", "bash", "--fetch=maybe")

    def test_raises_when_a_negated_boolean_flag_has_a_value(self):
        with self.assertRaisesRegex(CLIUsageError, "does not take a value"):
            self.parse("sample", "bash", "--no-fetch=true")

    def test_converts_an_integer_flag(self):
        parsed = self.parse("sample", "bash", "--count", "3")

        self.assertEqual({"count": 3}, parsed.kwargs)

    def test_raises_for_a_non_integer_flag_value(self):
        with self.assertRaisesRegex(CLIUsageError, "Invalid value for --count: 'three'"):
            self.parse("sample", "bash", "--count", "three")

    def test_validates_flag_choices(self):
        with self.assertRaisesRegex(CLIUsageError, "Invalid value for --shell: 'pwsh'"):
            self.parse("sample", "bash", "--shell", "pwsh")

    def test_raises_when_a_value_flag_is_the_last_token(self):
        with self.assertRaisesRegex(CLIUsageError, "Flag --cmd requires a value"):
            self.parse("sample", "bash", "--cmd")

    def test_raises_when_a_value_flag_is_followed_by_another_flag(self):
        with self.assertRaisesRegex(CLIUsageError, "Flag --cmd requires a value"):
            self.parse("sample", "bash", "--cmd", "--count", "3")

    def test_raises_for_an_unknown_flag(self):
        with self.assertRaisesRegex(CLIUsageError, "Unknown flag: --bogus") as context:
            self.parse("rm", "feature", "--bogus")

        self.assertEqual("remove", context.exception.command_name)

    def test_raises_for_a_multi_character_short_flag(self):
        with self.assertRaisesRegex(CLIUsageError, "Unknown flag: -fd"):
            self.parse("rm", "feature", "-fd")

    def test_raises_for_a_long_flag_that_only_matches_a_short_name(self):
        with self.assertRaisesRegex(CLIUsageError, "Unknown flag: --f"):
            self.parse("rm", "feature", "--f")

    def test_keeps_a_multiline_inline_value_for_long_and_short_flags(self):
        for token in ("--cmd=first\nsecond", "-c=first\nsecond"):
            with self.subTest(token=token):
                parsed = self.parse("sample", "bash", token)

                self.assertEqual("first\nsecond", parsed.kwargs["cmd"])

    def test_parses_an_empty_inline_value(self):
        parsed = self.parse("sample", "bash", "--cmd=")

        self.assertEqual({"cmd": ""}, parsed.kwargs)

    def test_lets_the_last_repeated_flag_win(self):
        parsed = self.parse("sample", "bash", "--count", "3", "--count", "4")

        self.assertEqual({"count": 4}, parsed.kwargs)

    def test_accepts_an_inline_value_that_looks_like_a_global_flag(self):
        parsed = self.parse("sample", "bash", "--cmd=-h")

        self.assertEqual({"cmd": "-h"}, parsed.kwargs)

    def test_requires_the_inline_form_for_a_negative_integer(self):
        self.assertEqual({"count": -3}, self.parse("sample", "bash", "--count=-3").kwargs)

        with self.assertRaisesRegex(CLIUsageError, "Flag --count requires a value"):
            self.parse("sample", "bash", "--count", "-3")

    def test_raises_when_the_passthrough_separator_follows_a_value_flag(self):
        with self.assertRaisesRegex(CLIUsageError, "Flag --cmd requires a value"):
            self.parse("sample", "bash", "--cmd", "--")

    def test_does_not_consume_a_positional_as_a_negated_boolean(self):
        parsed = self.parse("sample", "bash", "--no-fetch", "extra")

        self.assertEqual({"fetch": False}, parsed.kwargs)
        self.assertEqual(("bash", "extra"), parsed.args)

    def test_treats_a_positional_equal_to_a_command_name_as_a_positional(self):
        parsed = self.parse("create", "create")

        self.assertEqual("create", parsed.command_name)
        self.assertEqual(("create",), parsed.args)

    def test_raises_for_extra_tokens_after_the_help_subcommand(self):
        with self.assertRaisesRegex(CLIUsageError, "Unexpected argument: junk"):
            self.parse("help", "rm", "junk")

    def test_raises_for_a_bare_dash_command(self):
        with self.assertRaisesRegex(CLIUsageError, "Unknown command '-'."):
            self.parse("-")

    def test_does_not_consume_positionals_as_flag_values(self):
        parsed = self.parse("sample", "bash", "--cmd", "mytf", "extra")

        self.assertEqual({"cmd": "mytf"}, parsed.kwargs)
        self.assertEqual(("bash", "extra"), parsed.args)
