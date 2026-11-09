import importlib.util
import os
import py_compile
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from unittest import TestCase
from unittest.mock import patch

from treefort.cli_codegen import (
    build_manifest,
    format_command_help,
    format_root_help,
    main,
    parse_docstring,
    write_generated_module,
)
from treefort.cli_manifest import CommandSpec, ParamSpec
from treefort.command import Arg, Command, Completion, command
from treefort.errors import AliasConflictError


@command("sample", aliases=("sm",))
class SampleCommand(Command):
    """Sample command summary

    Sample description line one
    and line two.

    Examples:
        treefort sample thing

    :param thing: The thing to sample
    :param mode: How to sample it
    :param verbose_output: Enable verbose output
    :param count: How many to sample
    :type count: int
    """

    def __call__(
        self,
        thing: str,
        mode: Annotated[Literal["fast", "slow"], Arg()] = "fast",
        *,
        verbose_output: Annotated[bool, Arg(short="-V")] = False,
        count: Annotated[int, Arg(short="-c")] = 1,
    ):
        pass


@command("other")
class OtherCommand(Command):
    """Other command summary"""

    def __call__(
        self,
        name: Annotated[str | None, Arg(complete=Completion.WORKTREE)] = None,
        *,
        fetch: Annotated[bool, Arg(short="-f")] = True,
    ):
        pass


@command("twin")
class TwinCommand(Command):
    """Twin command summary"""

    def __call__(self, thing: str):
        pass


@command("duplicate", aliases=("sm",))
class DuplicateAliasCommand(Command):
    """A command whose alias collides"""

    def __call__(self, thing: str):
        pass


class ParseDocstringTestCase(TestCase):
    def test_returns_empty_values_for_no_docstring(self):
        self.assertEqual(("", "", {}), parse_docstring(None))

    def test_splits_the_summary_from_the_description(self):
        summary, description, _ = parse_docstring("First line\nsecond line\n\nBody paragraph.\n")

        self.assertEqual("First line second line", summary)
        self.assertEqual("Body paragraph.", description)

    def test_extracts_param_descriptions(self):
        _, _, params = parse_docstring(
            "Summary\n\n:param thing: The thing\n:param other: The other\n"
        )

        self.assertEqual({"thing": "The thing", "other": "The other"}, params)

    def test_collapses_param_continuation_lines(self):
        _, _, params = parse_docstring("Summary\n\n:param thing: The thing\n    that continues\n")

        self.assertEqual({"thing": "The thing that continues"}, params)

    def test_ignores_type_directives(self):
        _, _, params = parse_docstring("Summary\n\n:param thing: The thing\n:type thing: str\n")

        self.assertEqual({"thing": "The thing"}, params)

    def test_drops_prose_that_follows_parameter_help(self):
        _, _, params = parse_docstring(
            "Summary\n\n:param thing: The thing\n\nExamples:\n    treefort sample x\n"
        )

        self.assertEqual({"thing": "The thing"}, params)

    def test_drops_a_field_directive_that_follows_parameter_help(self):
        _, _, params = parse_docstring(
            "Summary\n\n:param thing: The thing\n:example: treefort sample x\n"
        )

        self.assertEqual({"thing": "The thing"}, params)

    def test_collapses_a_param_without_inline_text(self):
        _, _, params = parse_docstring("Summary\n\n:param thing:\n    Only a continuation\n")

        self.assertEqual({"thing": "Only a continuation"}, params)

    def test_treats_a_directive_only_docstring_as_having_no_summary(self):
        summary, description, params = parse_docstring(":param thing: The thing\n:type thing: str")

        self.assertEqual("", summary)
        self.assertEqual("", description)
        self.assertEqual({"thing": "The thing"}, params)

    def test_documents_a_variadic_parameter(self):
        _, _, params = parse_docstring("Summary\n\n:param *paths: The paths to run it in\n")

        self.assertEqual({"paths": "The paths to run it in"}, params)

    def test_keeps_examples_in_the_description(self):
        _, description, _ = parse_docstring(
            "Summary\n\nExamples:\n    treefort sample x\n\n:param thing: The thing\n"
        )

        self.assertEqual("Examples:\n    treefort sample x", description)


class BuildManifestTestCase(TestCase):
    def manifest(self, *classes):
        return build_manifest(command_classes=classes)

    def test_includes_every_command_sorted_alongside_the_builtin_commands(self):
        manifest = self.manifest(SampleCommand, OtherCommand)

        self.assertEqual(["aliases", "other", "sample", "version"], sorted(manifest.commands))

    def test_uses_the_program_name_and_docstring(self):
        manifest = self.manifest(SampleCommand)

        self.assertEqual("treefort", manifest.prog_name)
        self.assertEqual("CLI tool for managing Git worktrees", manifest.summary)
        self.assertIn("requires the Github CLI", manifest.description)

    def test_extracts_literal_choices(self):
        spec = self.manifest(SampleCommand).commands["sample"]
        mode = next(param for param in spec.params if param.name == "mode")

        self.assertEqual(("fast", "slow"), mode.choices)
        self.assertEqual("str", mode.value_type)
        self.assertFalse(mode.required)
        self.assertEqual("fast", mode.default)

    def test_marks_nullable_optional_positionals(self):
        spec = self.manifest(OtherCommand).commands["other"]
        name = next(param for param in spec.params if param.name == "name")

        self.assertEqual("positional", name.kind)
        self.assertTrue(name.nullable)
        self.assertFalse(name.required)
        self.assertEqual("worktree", name.completion)
        self.assertEqual("NAME", name.cli_name)

    def test_marks_a_variadic_positional_as_optional(self):
        @command("variadic")
        class VariadicCommand(Command):
            def __call__(self, *extras: str):
                pass

        spec = self.manifest(VariadicCommand).commands["variadic"]
        extras = spec.params[0]

        self.assertEqual("variadic_positional", extras.kind)
        self.assertFalse(extras.required)
        self.assertFalse(extras.has_default)
        self.assertIsNone(extras.default)
        self.assertIn("treefort variadic [EXTRAS]...", spec.help_text)

    def test_raises_for_a_keyword_only_flag_without_a_default(self):
        @command("required")
        class RequiredFlagCommand(Command):
            def __call__(self, *, target: str):
                pass

        with self.assertRaisesRegex(ValueError, "flag parameter target must declare a default"):
            self.manifest(RequiredFlagCommand)

    def test_extracts_keyword_only_flags(self):
        spec = self.manifest(OtherCommand).commands["other"]
        fetch = next(param for param in spec.params if param.name == "fetch")

        self.assertEqual("flag", fetch.kind)
        self.assertEqual("--fetch", fetch.cli_name)
        self.assertEqual("-f", fetch.short)
        self.assertIs(True, fetch.default)
        self.assertEqual("bool", fetch.value_type)

    def test_extracts_param_descriptions(self):
        spec = self.manifest(SampleCommand).commands["sample"]
        thing = next(param for param in spec.params if param.name == "thing")

        self.assertEqual("The thing to sample", thing.description)

    def test_builds_the_alias_map(self):
        manifest = self.manifest(SampleCommand, TwinCommand)

        self.assertEqual({"sm": "sample"}, manifest.alias_map)

    def test_renders_completions_for_every_shell(self):
        manifest = self.manifest(SampleCommand)

        self.assertEqual({"bash", "zsh", "fish"}, set(manifest.shell_completions))
        self.assertIn("_treefort_complete", manifest.shell_completions["bash"])

    def test_raises_when_an_alias_collides_with_another_alias(self):
        with self.assertRaisesRegex(AliasConflictError, "alias sm"):
            self.manifest(SampleCommand, DuplicateAliasCommand)

    def test_raises_when_an_alias_shadows_a_command_name(self):
        @command("shadow", aliases=("twin",))
        class ShadowCommand(Command):
            def __call__(self, thing: str):
                pass

        with self.assertRaisesRegex(AliasConflictError, "conflicts with command twin"):
            self.manifest(ShadowCommand, TwinCommand)

    def test_raises_when_two_flags_share_a_short_flag(self):
        @command("clash")
        class ClashCommand(Command):
            def __call__(
                self,
                *,
                one: Annotated[bool, Arg(short="-x")] = False,
                two: Annotated[bool, Arg(short="-x")] = False,
            ):
                pass

        with self.assertRaisesRegex(ValueError, "duplicate flag -x"):
            self.manifest(ClashCommand)

    def test_raises_for_a_multi_character_short_flag(self):
        @command("longshort")
        class LongShortCommand(Command):
            def __call__(self, *, one: Annotated[bool, Arg(short="-fb")] = False):
                pass

        with self.assertRaisesRegex(ValueError, "invalid short flag"):
            self.manifest(LongShortCommand)

    def test_raises_for_a_short_flag_on_a_positional(self):
        @command("positional")
        class PositionalCommand(Command):
            def __call__(self, one: Annotated[str, Arg(short="-o")]):
                pass

        with self.assertRaisesRegex(ValueError, "positional parameter one"):
            self.manifest(PositionalCommand)

    def test_raises_when_a_negation_shadows_another_flag(self):
        @command("negated")
        class NegatedCommand(Command):
            def __call__(
                self,
                *,
                fetch: bool = True,
                no_fetch: bool = False,
            ):
                pass

        with self.assertRaisesRegex(ValueError, "duplicate flag --no-fetch"):
            self.manifest(NegatedCommand)

    def test_raises_for_a_flag_that_shadows_a_global_short_flag(self):
        @command("global")
        class GlobalShortCommand(Command):
            def __call__(self, *, verbose_flag: Annotated[bool, Arg(short="-v")] = False):
                pass

        with self.assertRaisesRegex(ValueError, "flag -v is reserved by the global flags"):
            self.manifest(GlobalShortCommand)

    def test_raises_for_a_flag_that_shadows_a_global_long_flag(self):
        @command("global")
        class GlobalLongCommand(Command):
            def __call__(self, *, verbose: bool = False):
                pass

        with self.assertRaisesRegex(ValueError, "flag --verbose is reserved by the global flags"):
            self.manifest(GlobalLongCommand)

    def test_raises_when_a_default_false_negation_shadows_another_flag(self):
        # The parser accepts --no-<flag> for every boolean, not only for default-true ones
        @command("negated")
        class DefaultFalseNegationCommand(Command):
            def __call__(self, *, fetch: bool = False, no_fetch: bool = False):
                pass

        with self.assertRaisesRegex(ValueError, "duplicate flag --no-fetch"):
            self.manifest(DefaultFalseNegationCommand)

    def test_raises_for_a_boolean_positional(self):
        @command("boolean")
        class BooleanPositionalCommand(Command):
            def __call__(self, flag: bool):
                pass

        with self.assertRaisesRegex(ValueError, "positional parameter flag cannot be a boolean"):
            self.manifest(BooleanPositionalCommand)

    def test_raises_for_a_duplicate_command_name(self):
        @command("sample")
        class DuplicateCommand(Command):
            def __call__(self, thing: str):
                pass

        with self.assertRaisesRegex(ValueError, "Duplicate command name: sample"):
            self.manifest(SampleCommand, DuplicateCommand)

    def test_raises_for_a_command_that_shadows_a_builtin_command(self):
        @command("version")
        class VersionCommand(Command):
            def __call__(self, thing: str):
                pass

        with self.assertRaisesRegex(ValueError, "Duplicate command name: version"):
            self.manifest(VersionCommand)

    def test_raises_for_a_short_flag_with_a_trailing_newline(self):
        @command("newline")
        class NewlineCommand(Command):
            def __call__(self, *, flag: Annotated[bool, Arg(short="-x\n")] = False):
                pass

        with self.assertRaisesRegex(ValueError, "invalid short flag"):
            self.manifest(NewlineCommand)

    def test_accepts_an_annotated_optional(self):
        @command("nested")
        class NestedCommand(Command):
            def __call__(self, *, flag: Annotated[bool, Arg(short="-x")] | None = None):
                pass

        spec = self.manifest(NestedCommand).commands["nested"]

        self.assertEqual("-x", spec.params[0].short)
        self.assertTrue(spec.params[0].nullable)
        self.assertEqual("bool", spec.params[0].value_type)

    def test_raises_for_an_alias_equal_to_its_own_name(self):
        @command("selfalias", aliases=("selfalias",))
        class SelfAliasCommand(Command):
            def __call__(self, thing: str):
                pass

        with self.assertRaisesRegex(AliasConflictError, "declares its own name as an alias"):
            self.manifest(SelfAliasCommand)

    def test_creates_no_configuration_directory(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"HOME": home}):
                build_manifest()

            self.assertEqual([], list(Path(home).iterdir()))

    def test_does_not_instantiate_the_runtime(self):
        with patch("treefort.runtime.Runtime.__init__", side_effect=AssertionError("boom")):
            manifest = self.manifest(SampleCommand, OtherCommand)

        self.assertIn("sample", manifest.commands)

    def test_is_deterministic(self):
        self.assertEqual(
            self.manifest(SampleCommand, OtherCommand), self.manifest(SampleCommand, OtherCommand)
        )

    def test_builds_the_real_commands(self):
        manifest = build_manifest()

        self.assertEqual(
            ["aliases", "checkout", "create", "init", "install", "prune", "remove", "version"],
            sorted(manifest.commands),
        )
        self.assertEqual({"rm": "remove"}, manifest.alias_map)
        self.assertIn("remove (rm)", manifest.root_help_text)


class FormatRootHelpTestCase(TestCase):
    def spec(self, name, summary, aliases=()):
        return CommandSpec(
            name=name, aliases=aliases, summary=summary, description="", params=(), help_text=""
        )

    def test_renders_name_synopsis_and_commands(self):
        text = format_root_help(
            "treefort",
            "CLI tool for managing Git worktrees",
            "Extra detail.",
            {"remove": self.spec("remove", "Remove a worktree", ("rm",))},
        )

        self.assertIn("NAME\n    treefort - CLI tool for managing Git worktrees", text)
        self.assertIn("SYNOPSIS\n    treefort <command> [args...] [flags...]", text)
        self.assertIn("DESCRIPTION\n    Extra detail.", text)
        self.assertIn("COMMANDS\n    COMMAND is one of the following:", text)
        self.assertIn("       remove (rm)\n         Remove a worktree", text)
        self.assertIn("GLOBAL FLAGS", text)
        self.assertIn("    -h, --help\n        Show this help message and exit", text)
        self.assertIn("    -v, --verbose\n        Enable verbose debug logging", text)

    def test_renders_commands_in_alphabetical_order(self):
        text = format_root_help(
            "treefort",
            "Summary",
            "",
            {
                "version": self.spec("version", "Outputs the version"),
                "create": self.spec("create", "Create a worktree"),
                "aliases": self.spec("aliases", "Outputs the aliases"),
            },
        )

        self.assertLess(text.index("       aliases"), text.index("       create"))
        self.assertLess(text.index("       create"), text.index("       version"))

    def test_renders_the_exact_layout(self):
        text = format_root_help(
            "treefort",
            "CLI tool for managing Git worktrees",
            "Extra detail.",
            {
                "create": self.spec("create", "Create a new worktree"),
                "remove": self.spec("remove", "Remove a worktree", ("rm",)),
            },
        )

        self.assertEqual(
            "NAME\n"
            "    treefort - CLI tool for managing Git worktrees\n"
            "\n"
            "SYNOPSIS\n"
            "    treefort <command> [args...] [flags...]\n"
            "\n"
            "DESCRIPTION\n"
            "    Extra detail.\n"
            "\n"
            "COMMANDS\n"
            "    COMMAND is one of the following:\n"
            "\n"
            "       create\n"
            "         Create a new worktree\n"
            "\n"
            "       remove (rm)\n"
            "         Remove a worktree\n"
            "\n"
            "GLOBAL FLAGS\n"
            "    -h, --help\n"
            "        Show this help message and exit\n"
            "    -v, --verbose\n"
            "        Enable verbose debug logging\n",
            text,
        )

    def test_omits_the_description_when_empty(self):
        text = format_root_help("treefort", "Summary", "", {})

        self.assertNotIn("DESCRIPTION", text)


class FormatCommandHelpTestCase(TestCase):
    def spec(self, **overrides):
        values = {
            "name": "create",
            "aliases": ("c",),
            "summary": "Create a new worktree",
            "description": "Longer explanation.",
            "params": (
                ParamSpec(
                    name="worktree_name",
                    cli_name="WORKTREE_NAME",
                    kind="positional",
                    value_type="str",
                    nullable=False,
                    required=True,
                    has_default=False,
                    default=None,
                    choices=(),
                    short=None,
                    completion="none",
                    description="The name of the worktree",
                ),
                ParamSpec(
                    name="mode",
                    cli_name="--mode",
                    kind="flag",
                    value_type="str",
                    nullable=True,
                    required=False,
                    has_default=True,
                    default="fast",
                    choices=("fast", "slow"),
                    short="-m",
                    completion="none",
                    description="How to create it",
                ),
                ParamSpec(
                    name="yes",
                    cli_name="--yes",
                    kind="flag",
                    value_type="bool",
                    nullable=False,
                    required=False,
                    has_default=True,
                    default=False,
                    choices=(),
                    short="-y",
                    completion="none",
                    description="Skip the prompts",
                ),
            ),
            "help_text": "",
        }
        values.update(overrides)
        return CommandSpec(**values)

    def test_renders_the_sections_in_order(self):
        text = format_command_help("treefort", self.spec())

        self.assertTrue(text.startswith("NAME\n    treefort create - Create a new worktree"))
        self.assertIn("SYNOPSIS\n    treefort create WORKTREE_NAME <flags>", text)
        self.assertIn("ALIASES\n    c", text)
        self.assertIn("DESCRIPTION\n    Longer explanation.", text)
        self.assertIn("POSITIONAL ARGUMENTS\n    WORKTREE_NAME", text)
        self.assertIn(
            "        Type: str\n        The name of the worktree",
            text,
        )
        self.assertIn(
            "    -m, --mode=MODE\n        Type: str | None\n"
            "        Options: fast, slow\n        Default: 'fast'",
            text,
        )
        self.assertIn("    -y, --yes\n        Type: bool\n        Default: False", text)
        self.assertIn("    -h, --help\n        Show this help message and exit", text)
        self.assertIn("    -v, --verbose\n        Enable verbose debug logging", text)

    def test_renders_the_exact_layout(self):
        text = format_command_help("treefort", self.spec())

        self.assertEqual(
            "NAME\n"
            "    treefort create - Create a new worktree\n"
            "\n"
            "SYNOPSIS\n"
            "    treefort create WORKTREE_NAME <flags>\n"
            "\n"
            "ALIASES\n"
            "    c\n"
            "\n"
            "DESCRIPTION\n"
            "    Longer explanation.\n"
            "\n"
            "POSITIONAL ARGUMENTS\n"
            "    WORKTREE_NAME\n"
            "        Type: str\n"
            "        The name of the worktree\n"
            "\n"
            "FLAGS\n"
            "    -m, --mode=MODE\n"
            "        Type: str | None\n"
            "        Options: fast, slow\n"
            "        Default: 'fast'\n"
            "        How to create it\n"
            "    -y, --yes\n"
            "        Type: bool\n"
            "        Default: False\n"
            "        Skip the prompts\n"
            "    -h, --help\n"
            "        Show this help message and exit\n"
            "    -v, --verbose\n"
            "        Enable verbose debug logging\n",
            text,
        )

    def test_renders_the_negation_of_a_default_true_boolean(self):
        spec = self.spec(
            params=(
                ParamSpec(
                    name="fetch",
                    cli_name="--fetch",
                    kind="flag",
                    value_type="bool",
                    nullable=False,
                    required=False,
                    has_default=True,
                    default=True,
                    choices=(),
                    short=None,
                    completion="none",
                    description="",
                ),
            )
        )

        self.assertIn("    --fetch, --no-fetch", format_command_help("treefort", spec))

    def test_omits_optional_sections(self):
        spec = self.spec(aliases=(), description="", params=())

        text = format_command_help("treefort", spec)

        self.assertNotIn("ALIASES", text)
        self.assertNotIn("DESCRIPTION", text)
        self.assertNotIn("POSITIONAL ARGUMENTS", text)
        self.assertIn("FLAGS", text)

    def test_renders_variadic_positionals_in_the_synopsis(self):
        spec = CommandSpec(
            name="for-each",
            aliases=(),
            summary="Run a command",
            description="",
            params=(
                ParamSpec(
                    name="command",
                    cli_name="COMMAND",
                    kind="variadic_positional",
                    value_type="str",
                    nullable=False,
                    required=False,
                    has_default=False,
                    default=None,
                    choices=(),
                    short=None,
                    completion="none",
                    description="",
                ),
            ),
            help_text="",
        )

        self.assertIn("treefort for-each [COMMAND]...", format_command_help("treefort", spec))


class WriteGeneratedModuleTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp_dir.name) / "treefort" / "_cli_generated.py"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def load(self):
        spec = importlib.util.spec_from_file_location("generated_under_test", self.output)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_writes_an_importable_module(self):
        manifest = build_manifest(command_classes=(SampleCommand,))
        write_generated_module(self.output, manifest)

        module = self.load()

        self.assertEqual(manifest, module.MANIFEST)
        self.assertIsInstance(module._SOURCE_MTIME_NS, int)
        self.assertGreater(module._SOURCE_MTIME_NS, 0)

    def test_honors_the_umask_for_the_output_mode(self):
        previous = os.umask(0o022)
        try:
            write_generated_module(self.output, build_manifest(command_classes=(SampleCommand,)))
        finally:
            os.umask(previous)

        self.assertEqual(0o644, self.output.stat().st_mode & 0o777)

    def test_keeps_the_previous_file_when_the_replace_fails(self):
        write_generated_module(self.output, build_manifest(command_classes=(SampleCommand,)))
        previous = self.output.read_bytes()

        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_generated_module(self.output, build_manifest(command_classes=(OtherCommand,)))

        self.assertEqual(previous, self.output.read_bytes())
        self.assertEqual(
            ["_cli_generated.py"], sorted(path.name for path in self.output.parent.iterdir())
        )

    def test_output_compiles(self):
        write_generated_module(self.output, build_manifest(command_classes=(SampleCommand,)))

        py_compile.compile(str(self.output), doraise=True)

    def test_is_byte_stable(self):
        manifest = build_manifest(command_classes=(SampleCommand,))
        write_generated_module(self.output, manifest)
        first = self.output.read_bytes()
        write_generated_module(self.output, manifest)

        self.assertEqual(first, self.output.read_bytes())

    def test_leaves_no_temporary_files(self):
        write_generated_module(self.output, build_manifest(command_classes=(SampleCommand,)))

        self.assertEqual(
            ["_cli_generated.py"], sorted(path.name for path in self.output.parent.iterdir())
        )

    def test_manifest_is_up_to_date(self):
        # Guards against drift between the command classes and the generated manifest
        write_generated_module(
            self.output, build_manifest(command_classes=(SampleCommand, OtherCommand))
        )

        self.assertEqual(
            build_manifest(command_classes=(SampleCommand, OtherCommand)), self.load().MANIFEST
        )


class MainTestCase(TestCase):
    def test_writes_the_requested_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "_cli_generated.py"

            main(["--output", str(output)])

            self.assertTrue(output.exists())
            self.assertIn("MANIFEST = CLIManifest(", output.read_text(encoding="utf-8"))


class ManifestDriftTestCase(TestCase):
    """Compares the checked-out generated module against the live command classes."""

    @property
    def generated_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / "src" / "treefort" / "_cli_generated.py"

    def test_manifest_is_up_to_date(self):
        if not self.generated_path.exists():
            self.skipTest("run `make codegen` to compare against the generated manifest")

        spec = importlib.util.spec_from_file_location("generated_drift_check", self.generated_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(build_manifest(), module.MANIFEST)
