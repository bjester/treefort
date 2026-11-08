import re
from collections.abc import Sequence
from dataclasses import dataclass

from treefort.cli_manifest import CLIManifest, CommandSpec, ParamSpec
from treefort.errors import CLIUsageError

PASSTHROUGH_SEPARATOR = "--"
VERBOSE_FLAGS = ("-v", "--verbose")
HELP_FLAGS = ("-h", "--help")
TRUE_VALUES = ("true", "1", "yes")
FALSE_VALUES = ("false", "0", "no")
SHORT_FLAG_RE = re.compile(r"^-[a-zA-Z](=.*)?$")


@dataclass(frozen=True)
class ParsedInvocation:
    """The validated result of parsing raw CLI tokens."""

    command_name: str | None
    args: tuple[object, ...]
    kwargs: dict[str, object]
    verbose: bool
    help_requested: bool


def parse_cli_args(manifest: CLIManifest, argv: Sequence[str]) -> ParsedInvocation:
    """Parse raw CLI tokens into a validated invocation.

    Global flags are recognized anywhere before the ``--`` separator, command aliases resolve to
    their canonical name, and every value is validated against the command's ``ParamSpec``. No
    token is ever evaluated as a Python literal, so ``125``, ``1.10``, and ``True`` stay strings.

    :param manifest: The CLI manifest describing every command and parameter
    :param argv: The raw arguments, excluding the program name
    :return: The parsed invocation
    :raises CLIUsageError: When the arguments or flags are invalid
    """
    pre_sep, passthrough = _split_passthrough(argv)
    verbose, help_requested, tokens = _extract_global_flags(pre_sep)

    if not tokens:
        if passthrough:
            raise CLIUsageError("Missing command.")
        return ParsedInvocation(None, (), {}, verbose, True)

    if tokens[0] == "help":
        return ParsedInvocation(_resolve_help_target(manifest, tokens[1:]), (), {}, verbose, True)

    if _is_flag_token(tokens[0]):
        raise CLIUsageError(f"Unknown flag: {tokens[0]}")

    command_name = _resolve_command_name(manifest, tokens[0])
    if help_requested:
        return ParsedInvocation(command_name, (), {}, verbose, True)

    args, kwargs = _bind_arguments(manifest.commands[command_name], tokens[1:], passthrough)
    return ParsedInvocation(command_name, args, kwargs, verbose, False)


def _split_passthrough(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split the arguments at the first ``--`` separator."""
    tokens = list(argv)
    if PASSTHROUGH_SEPARATOR not in tokens:
        return tokens, []

    index = tokens.index(PASSTHROUGH_SEPARATOR)
    return tokens[:index], tokens[index + 1 :]


def _extract_global_flags(tokens: Sequence[str]) -> tuple[bool, bool, list[str]]:
    """Remove the global verbose and help flags from the tokens."""
    verbose = False
    help_requested = False
    remaining = []
    for token in tokens:
        if token in VERBOSE_FLAGS:
            verbose = True
        elif token in HELP_FLAGS:
            help_requested = True
        else:
            remaining.append(token)
    return verbose, help_requested, remaining


def _resolve_help_target(manifest: CLIManifest, tokens: Sequence[str]) -> str | None:
    """Resolve the command named by ``help <command>``, if any."""
    if not tokens:
        return None
    if len(tokens) > 1:
        raise CLIUsageError(f"Unexpected argument: {tokens[1]}")
    return _resolve_command_name(manifest, tokens[0])


def _resolve_command_name(manifest: CLIManifest, token: str) -> str:
    """Resolve a canonical command name or alias to a canonical command name."""
    command_name = manifest.alias_map.get(token, token)
    if command_name not in manifest.commands:
        raise CLIUsageError(f"Unknown command '{token}'.")
    return command_name


def _bind_arguments(
    spec: CommandSpec, tokens: Sequence[str], passthrough: Sequence[str]
) -> tuple[tuple[object, ...], dict[str, object]]:
    """Bind flag tokens to keyword arguments and the rest to positional arguments."""
    lookup = _flag_lookup(spec)
    positionals: list[str] = []
    kwargs: dict[str, object] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not _is_flag_token(token):
            positionals.append(token)
            index += 1
            continue
        index = _consume_flag(spec, lookup, tokens, index, kwargs)

    positionals.extend(passthrough)
    return _bind_positionals(spec, positionals), kwargs


def _flag_lookup(spec: CommandSpec) -> dict[str, tuple[ParamSpec, bool]]:
    """Map every accepted flag spelling, dashes included, to its parameter and negation state."""
    lookup: dict[str, tuple[ParamSpec, bool]] = {}
    for param in spec.params:
        if param.kind != "flag":
            continue
        kebab = param.name.replace("_", "-")
        for name in (kebab, param.name):
            lookup[f"--{name}"] = (param, False)
        if param.value_type == "bool":
            lookup[f"--no-{kebab}"] = (param, True)
            lookup[f"--no_{param.name}"] = (param, True)
        if param.short is not None:
            lookup[param.short] = (param, False)
    return lookup


def _is_flag_token(token: str) -> bool:
    """Return whether a token is a flag rather than a positional.

    A bare ``-`` is a positional so that commands can accept it as a shortcut value.
    """
    return token.startswith("-") and token != "-"


def _split_flag_token(token: str) -> tuple[str, str | None]:
    """Split a flag token into its dashed spelling and its inline value, if any."""
    flag_token, separator, value = token.partition("=")
    return flag_token, (value if separator else None)


def _consume_flag(
    spec: CommandSpec,
    lookup: dict[str, tuple[ParamSpec, bool]],
    tokens: Sequence[str],
    index: int,
    kwargs: dict[str, object],
) -> int:
    """Store one flag's value in ``kwargs`` and return the index of the next token."""
    token = tokens[index]
    if not token.startswith("--") and SHORT_FLAG_RE.match(token) is None:
        raise CLIUsageError(f"Unknown flag: {token}", command_name=spec.name)

    flag_token, inline_value = _split_flag_token(token)
    entry = lookup.get(flag_token)
    if entry is None:
        raise CLIUsageError(f"Unknown flag: {token}", command_name=spec.name)

    param, negated = entry
    if param.value_type != "bool":
        value, next_index = _flag_value(param, inline_value, tokens, index, spec)
        kwargs[param.name] = _convert(param, value, spec)
        return next_index

    kwargs[param.name] = _boolean_value(param, negated, inline_value, spec)
    return index + 1


def _flag_value(
    param: ParamSpec,
    inline_value: str | None,
    tokens: Sequence[str],
    index: int,
    spec: CommandSpec,
) -> tuple[str, int]:
    """Return a value flag's value and the index of the next token."""
    if inline_value is not None:
        return inline_value, index + 1

    following = tokens[index + 1] if index + 1 < len(tokens) else None
    if following is None or _is_flag_token(following):
        raise CLIUsageError(f"Flag {param.cli_name} requires a value", command_name=spec.name)
    return following, index + 2


def _boolean_value(
    param: ParamSpec, negated: bool, inline_value: str | None, spec: CommandSpec
) -> bool:
    """Return a boolean flag's value from its negation or inline value."""
    if negated:
        if inline_value is not None:
            raise CLIUsageError(
                f"Flag --no-{param.name.replace('_', '-')} does not take a value: '{inline_value}'",
                command_name=spec.name,
            )
        return False

    if inline_value is None:
        return True

    normalized = inline_value.lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False

    raise CLIUsageError(
        f"Invalid value for {param.cli_name}: '{inline_value}' (expected true or false)",
        command_name=spec.name,
    )


def _convert(param: ParamSpec, raw: str, spec: CommandSpec) -> object:
    """Convert a raw token to the parameter's value type and validate its choices."""
    value: object = raw
    if param.value_type == "int":
        try:
            value = int(raw)
        except ValueError:
            raise CLIUsageError(
                f"Invalid value for {param.cli_name}: '{raw}' (expected an integer)",
                command_name=spec.name,
            ) from None

    if param.choices and str(value) not in param.choices:
        options = ", ".join(param.choices)
        raise CLIUsageError(
            f"Invalid value for {param.cli_name}: '{raw}' (choose from {options})",
            command_name=spec.name,
        )
    return value


def _bind_positionals(spec: CommandSpec, tokens: Sequence[str]) -> tuple[object, ...]:
    """Bind positional tokens to the command's positional parameters, in declaration order."""
    remaining = list(tokens)
    args: list[object] = []
    for param in spec.params:
        if param.kind == "flag":
            continue
        if param.kind == "variadic_positional":
            args.extend(_convert(param, token, spec) for token in remaining)
            remaining.clear()
            continue
        if not remaining:
            if param.required:
                raise CLIUsageError(
                    f"Missing required argument: {param.cli_name}", command_name=spec.name
                )
            continue
        args.append(_convert(param, remaining.pop(0), spec))

    if remaining:
        raise CLIUsageError(f"Unexpected argument: {remaining[0]}", command_name=spec.name)
    return tuple(args)
