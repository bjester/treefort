"""Build-time introspection and code generation for the CLI.

Run ``python -m treefort.cli_codegen --output src/treefort/_cli_generated.py``, which the
``codegen`` Make target does before packaging. ``cli_manifest`` imports this module lazily and
only for an unbuilt source checkout, so built artifacts can leave it out.
"""

import argparse
import inspect
import os
import re
import tempfile
import types
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Literal, Union, get_args, get_origin

from treefort.cli_manifest import CLIManifest, CommandSpec, ParamSpec, max_source_mtime_ns
from treefort.cli_parser import HELP_FLAGS, VERBOSE_FLAGS
from treefort.command import Arg, Command, Completion
from treefort.errors import AliasConflictError
from treefort.main import WorktreeCommands
from treefort.shells import render_all_completions

SHORT_FLAG_RE = re.compile(r"-[a-zA-Z]")
PARAM_DIRECTIVE_RE = re.compile(r"^:param\s+[*]{0,2}(\w+):\s*(.*)$")
FIELD_RE = re.compile(r"^:\w+")
# The parser answers these before a command sees them, so a command cannot declare them
RESERVED_FLAGS = (*HELP_FLAGS, *VERBOSE_FLAGS)
GLOBAL_FLAGS_HELP = (
    "    -h, --help",
    "        Show this help message and exit",
    "    -v, --verbose",
    "        Enable verbose debug logging",
)
MODULE_HEADER = '''"""Generated CLI manifest. Do not edit; run `make codegen` instead."""

from treefort.cli_manifest import CLIManifest, CommandSpec, ParamSpec

_SOURCE_MTIME_NS = {mtime_ns}

MANIFEST = {manifest}
'''


def parse_docstring(doc: str | None) -> tuple[str, str, dict[str, str]]:
    """Parse a Sphinx-style docstring into a summary, a description, and parameter help.

    The summary is the first paragraph, collapsed onto one line. The description is everything
    before the first directive, with its indentation preserved. Parameter help comes from
    ``:param <name>:`` entries, with continuation lines collapsed.

    :param doc: The raw docstring
    :return: The summary, the description, and the parameter descriptions by name
    """
    text = inspect.cleandoc(doc or "")
    if not text:
        return "", "", {}

    summary, rest = _split_summary(text.splitlines())
    description, params = _split_directives(rest)
    return summary, description, params


def _split_summary(lines: list[str]) -> tuple[str, list[str]]:
    if lines and FIELD_RE.match(lines[0].strip()):
        return "", lines

    summary = []
    index = 0
    while index < len(lines) and lines[index].strip():
        summary.append(lines[index].strip())
        index += 1
    return " ".join(summary), lines[index:]


def _split_directives(lines: list[str]) -> tuple[str, dict[str, str]]:
    description = []
    params: dict[str, list[str]] = {}
    current = None
    in_directives = False
    for line in lines:
        stripped = line.strip()
        match = PARAM_DIRECTIVE_RE.match(stripped)
        if match:
            in_directives = True
            current = match.group(1)
            params[current] = [match.group(2).strip()]
            continue
        if FIELD_RE.match(stripped):
            in_directives = True
            current = None
            continue
        if in_directives:
            if not stripped:
                current = None
            elif current is not None:
                params[current].append(stripped)
            continue
        description.append(line)

    collapsed = {name: " ".join(part for part in parts if part) for name, parts in params.items()}
    return "\n".join(description).strip(), collapsed


def build_manifest(command_classes: tuple[type[Command], ...] | None = None) -> CLIManifest:
    """Inspect the command classes and return the complete CLI manifest.

    Instantiating ``WorktreeCommands`` or ``Runtime`` is deliberately avoided so generating the
    manifest never creates a log directory or runs a configuration migration.

    :param command_classes: The command classes to inspect, defaulting to the registered ones
    :return: The populated manifest
    :raises AliasConflictError: When an alias collides with an alias or a command name
    :raises ValueError: When a parameter or flag declaration is invalid
    """
    prog_name = WorktreeCommands._name
    classes = WorktreeCommands._command_classes if command_classes is None else command_classes
    summary, description, _ = parse_docstring(WorktreeCommands.__doc__)

    specs: dict[str, CommandSpec] = {}
    for command_class in classes:
        _reject_duplicate(specs, command_class._name)
        specs[command_class._name] = _command_spec(
            prog_name,
            command_class._name,
            tuple(command_class._aliases),
            command_class.__call__,
            # ``inspect.getdoc`` on a class attribute resolves the metaclass docstring, so read
            # the method's own docstring first and fall back to the class docstring.
            getattr(command_class.__call__, "__doc__", None) or inspect.getdoc(command_class),
        )
    for name, method in _builtin_commands(WorktreeCommands):
        _reject_duplicate(specs, name)
        specs[name] = _command_spec(prog_name, name, (), method, method.__doc__)

    for spec in specs.values():
        _validate_params(spec)

    commands = {name: specs[name] for name in sorted(specs)}
    alias_map = _build_alias_map(commands)
    return CLIManifest(
        prog_name=prog_name,
        summary=summary,
        description=description,
        commands=commands,
        alias_map=alias_map,
        root_help_text=format_root_help(prog_name, summary, description, commands),
        shell_completions=render_all_completions(commands, alias_map),
    )


def _reject_duplicate(specs: dict[str, CommandSpec], name: str) -> None:
    if name in specs:
        raise ValueError(f"Duplicate command name: {name}")


def _builtin_commands(cls: type) -> list[tuple[str, object]]:
    """Return the public methods declared directly on ``cls``, which are commands too."""
    return [
        (name, member)
        for name, member in vars(cls).items()
        if not name.startswith("_") and callable(member)
    ]


def _command_spec(
    prog_name: str,
    name: str,
    aliases: tuple[str, ...],
    func: Callable[..., object],
    doc: str | None,
) -> CommandSpec:
    summary, description, param_docs = parse_docstring(doc)
    params = tuple(_param_spec(parameter, param_docs) for parameter in _signature_parameters(func))
    spec = CommandSpec(
        name=name,
        aliases=aliases,
        summary=summary,
        description=description,
        params=params,
        help_text="",
    )
    return replace(spec, help_text=format_command_help(prog_name, spec))


def _signature_parameters(func) -> list[inspect.Parameter]:
    return [
        parameter
        for parameter in inspect.signature(func).parameters.values()
        if parameter.name != "self"
    ]


def _param_spec(parameter: inspect.Parameter, param_docs: dict[str, str]) -> ParamSpec:
    kind = _parameter_kind(parameter)
    value_type, nullable, choices, arg = _resolve_annotation(parameter.annotation)
    required = parameter.default is inspect.Parameter.empty
    name = parameter.name
    return ParamSpec(
        name=name,
        cli_name=f"--{name.replace('_', '-')}" if kind == "flag" else name.upper(),
        kind=kind,
        value_type=value_type,
        nullable=nullable,
        required=required,
        has_default=not required,
        default=None if required else _default_value(name, parameter.default),
        choices=choices,
        short=arg.short if arg is not None else None,
        completion=arg.complete.value if arg is not None else Completion.NONE.value,
        description=param_docs.get(name, ""),
    )


def _parameter_kind(parameter: inspect.Parameter) -> str:
    if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
        return "flag"
    if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
        return "variadic_positional"
    if parameter.kind is inspect.Parameter.VAR_KEYWORD:
        raise ValueError(f"Unsupported parameter: **{parameter.name}")
    return "positional"


def _default_value(name: str, default: object) -> str | bool | int | None:
    if default is None or isinstance(default, (str, bool, int)):
        return default
    raise ValueError(f"Unsupported default for parameter {name}: {default!r}")


def _resolve_annotation(annotation: object) -> tuple[str, bool, tuple[str, ...], Arg | None]:
    base = annotation
    arg = None
    nullable = False
    while True:
        base, found = _unwrap_annotated(base)
        arg = found or arg
        is_optional, inner = _unwrap_optional(base)
        if not is_optional:
            break
        nullable = True
        base = inner
    value_type, choices = _resolve_member(base)
    return value_type, nullable, choices, arg


def _unwrap_annotated(annotation: object) -> tuple[object, Arg | None]:
    if get_origin(annotation) is not Annotated:
        return annotation, None
    base, *metadata = get_args(annotation)
    arg = next((item for item in metadata if isinstance(item, Arg)), None)
    return base, arg


def _unwrap_optional(annotation: object) -> tuple[bool, object]:
    origin = get_origin(annotation)
    if origin is not Union and origin is not types.UnionType:
        return False, annotation
    members = [item for item in get_args(annotation) if item is not type(None)]
    if len(members) != 1:
        raise ValueError(f"Unsupported union type: {annotation!r}")
    return True, members[0]


def _resolve_member(member: object) -> tuple[str, tuple[str, ...]]:
    if get_origin(member) is not Literal:
        return _value_type(member), ()
    values = get_args(member)
    kinds = {type(value) for value in values}
    if len(kinds) != 1:
        raise ValueError(f"Literal choices must share one type: {member!r}")
    return _value_type(next(iter(kinds))), tuple(str(value) for value in values)


def _value_type(member: object) -> str:
    if member is str:
        return "str"
    if member is bool:
        return "bool"
    if member is int:
        return "int"
    raise ValueError(f"Unsupported CLI parameter type: {member!r}")


def _validate_params(spec: CommandSpec) -> None:
    seen: dict[str, str] = {}
    for param in spec.params:
        if param.kind != "flag":
            if param.short is not None:
                raise ValueError(
                    f"Command {spec.name}: positional parameter {param.name} "
                    "cannot declare a short flag"
                )
            if param.value_type == "bool":
                raise ValueError(
                    f"Command {spec.name}: positional parameter {param.name} cannot be a boolean"
                )
            continue
        if param.short is not None and SHORT_FLAG_RE.fullmatch(param.short) is None:
            raise ValueError(f"Command {spec.name}: invalid short flag {param.short}")
        for spelling in _accepted_spellings(param):
            if spelling in RESERVED_FLAGS:
                raise ValueError(
                    f"Command {spec.name}: flag {spelling} is reserved by the global flags"
                )
            if spelling in seen:
                raise ValueError(f"Command {spec.name}: duplicate flag {spelling}")
            seen[spelling] = param.name


def _accepted_spellings(param: ParamSpec) -> list[str]:
    """Return every spelling the runtime parser accepts for a flag.

    Mirrors ``cli_parser._flag_lookup``: a boolean answers to ``--no-<kebab>`` whatever its
    default, so two parameters can collide there without colliding on their own names.
    """
    spellings = [param.cli_name]
    if param.short is not None:
        spellings.append(param.short)
    if param.value_type == "bool":
        spellings.append(f"--no-{param.name.replace('_', '-')}")
    return spellings


def _build_alias_map(specs: dict[str, CommandSpec]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for name, spec in specs.items():
        for alias in spec.aliases:
            if alias == name:
                raise AliasConflictError(f"Command {name} declares its own name as an alias")
            if alias in specs:
                raise AliasConflictError(
                    f"Command {name} wants alias {alias} which conflicts with command {alias}"
                )
            if alias in alias_map:
                raise AliasConflictError(
                    f"Command {name} wants alias {alias} "
                    f"but it already exists for {alias_map[alias]}"
                )
            alias_map[alias] = name
    return alias_map


def format_root_help(
    prog_name: str, summary: str, description: str, commands: dict[str, CommandSpec]
) -> str:
    """Render the top-level help text.

    :param prog_name: The program name
    :param summary: The one-line program summary
    :param description: The program description, when there is one
    :param commands: The commands to list
    :return: The rendered help text
    """
    lines = [
        "NAME",
        f"    {prog_name} - {summary}",
        "",
        "SYNOPSIS",
        f"    {prog_name} <command> [args...] [flags...]",
    ]
    lines.extend(_description_section(description))
    lines.extend(["", "COMMANDS", "    COMMAND is one of the following:", ""])
    for spec in sorted(commands.values(), key=lambda item: item.name):
        label = spec.name if not spec.aliases else f"{spec.name} ({', '.join(spec.aliases)})"
        lines.append(f"       {label}")
        if spec.summary:
            lines.append(f"         {spec.summary}")
        lines.append("")
    lines.append("GLOBAL FLAGS")
    lines.extend(GLOBAL_FLAGS_HELP)
    return "\n".join(lines) + "\n"


def format_command_help(prog_name: str, spec: CommandSpec) -> str:
    """Render one command's help text.

    :param prog_name: The program name
    :param spec: The command to render
    :return: The rendered help text
    """
    lines = [
        "NAME",
        f"    {prog_name} {spec.name} - {spec.summary}",
        "",
        "SYNOPSIS",
        f"    {_synopsis(prog_name, spec)}",
    ]
    if spec.aliases:
        lines.extend(["", "ALIASES", f"    {', '.join(spec.aliases)}"])
    lines.extend(_description_section(spec.description))

    positionals = [param for param in spec.params if param.kind != "flag"]
    if positionals:
        lines.extend(["", "POSITIONAL ARGUMENTS"])
        for param in positionals:
            lines.append(f"    {param.cli_name}")
            lines.extend(
                _param_detail_lines(
                    param, include_default=param.has_default and param.default is not None
                )
            )

    lines.extend(["", "FLAGS"])
    for param in spec.params:
        if param.kind != "flag":
            continue
        lines.append(f"    {_flag_text(param)}")
        lines.extend(_param_detail_lines(param, include_default=True))
    lines.extend(GLOBAL_FLAGS_HELP)
    return "\n".join(lines) + "\n"


def _description_section(description: str) -> list[str]:
    if not description:
        return []
    body = [f"    {line}" if line else "" for line in description.splitlines()]
    return ["", "DESCRIPTION", *body]


def _synopsis(prog_name: str, spec: CommandSpec) -> str:
    tokens = [prog_name, spec.name]
    for param in spec.params:
        if param.kind == "flag":
            continue
        if param.kind == "variadic_positional":
            tokens.append(f"[{param.cli_name}]...")
        elif param.required:
            tokens.append(param.cli_name)
        else:
            tokens.append(f"[{param.cli_name}]")
    if any(param.kind == "flag" for param in spec.params):
        tokens.append("<flags>")
    return " ".join(tokens)


def _param_detail_lines(param: ParamSpec, include_default: bool) -> list[str]:
    lines = [f"        Type: {_type_text(param)}"]
    if param.choices:
        lines.append(f"        Options: {', '.join(param.choices)}")
    if include_default:
        lines.append(f"        Default: {param.default!r}")
    if param.description:
        lines.append(f"        {param.description}")
    return lines


def _type_text(param: ParamSpec) -> str:
    return f"{param.value_type} | None" if param.nullable else param.value_type


def _flag_text(param: ParamSpec) -> str:
    kebab = param.name.replace("_", "-")
    names = [param.short] if param.short is not None else []
    if param.value_type != "bool":
        names.append(f"--{kebab}={param.name.upper()}")
        return ", ".join(names)
    names.append(f"--{kebab}")
    if param.default is True:
        names.append(f"--no-{kebab}")
    return ", ".join(names)


def write_generated_module(output_path: Path, manifest: CLIManifest | None = None) -> None:
    """Write the generated module, replacing any previous copy atomically.

    :param output_path: Where to write the module
    :param manifest: The manifest to embed, built from the live commands when omitted
    """
    resolved = manifest if manifest is not None else build_manifest()
    source = MODULE_HEADER.format(mtime_ns=max_source_mtime_ns(), manifest=repr(resolved))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=output_path.parent, prefix=".cli_generated-", suffix=".py"
    )
    try:
        stream = os.fdopen(handle, "w", encoding="utf-8")
        handle = -1  # the stream owns the descriptor now
        with stream:
            stream.write(source)
        Path(temporary).chmod(_default_file_mode())
        Path(temporary).replace(output_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    finally:
        if handle != -1:
            os.close(handle)


def _default_file_mode() -> int:
    """Return the mode a newly created file would get, honoring the process umask."""
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def main(argv: list[str] | None = None) -> None:
    """Write the generated module at the requested path.

    :param argv: The command-line arguments, defaulting to ``sys.argv[1:]``
    """
    parser = argparse.ArgumentParser(prog="python -m treefort.cli_codegen", description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Path of the module to write")
    arguments = parser.parse_args(argv)
    write_generated_module(arguments.output)


if __name__ == "__main__":
    main()
