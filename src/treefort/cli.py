import logging
import re
import sys

import fire

from treefort.main import WorktreeCommands


def replace_alias(cli: WorktreeCommands):
    """
    Replaces an alias with its corresponding full subcommand name in the system
    arguments if the provided subcommand is an alias.

    This function inspects the command-line arguments and, if an alias is found
    as the first subcommand, replaces it with the full subcommand name as defined
    in the alias map of the provided `WorktreeCommands` instance.

    :param cli: An instance of WorktreeCommands containing the alias map used to
                resolve aliases to their corresponding full subcommand names.
    """
    subcommand = sys.argv[1] if len(sys.argv) > 1 else None
    if subcommand is not None and subcommand in cli._alias_map.keys():
        sys.argv[1] = cli._alias_map[subcommand]


def _is_flag(token: str) -> bool:
    """Whether fire treats the token as a flag rather than a positional argument.

    Mirrors fire's rule: a single letter, or any word, after one or two hyphens; a negative
    number such as ``-5`` stays a positional argument.
    """
    return bool(re.match(r"^-[a-zA-Z]", token) or token.startswith("--"))


def _flag_parameter(token: str, names: tuple[str, ...]) -> str | None:
    """The literal argument a flag token sets, if any.

    Mirrors fire's keyword resolution: the key is the token without leading hyphens and
    without any ``=value``, with hyphens mapped to underscores, and a single-character key is
    a shortcut for the one argument that starts with it.
    """
    key = token.lstrip("-").split("=", 1)[0].replace("-", "_")
    if key in names:
        return key
    if len(key) == 1:
        matches = [name for name in names if name.startswith(key)]
        if len(matches) == 1:
            return matches[0]
    return None


def _rewrite_flag(argv: list[str], index: int, names: tuple[str, ...], pending: list[str]) -> int:
    """Quote the value of one flag token in place, returning the next index to consider."""
    token = argv[index]
    parameter = _flag_parameter(token, names)
    if parameter in pending:
        pending.remove(parameter)
    next_token = argv[index + 1] if index + 1 < len(argv) else None
    if "=" in token:
        if parameter is not None:
            key, _, value = token.partition("=")
            argv[index] = f"{key}={value!r}"
        return index + 1
    if next_token is not None and not _is_flag(next_token):
        if parameter is not None:
            argv[index + 1] = repr(next_token)
        return index + 2
    return index + 1


def replace_literal_args(cli: WorktreeCommands):
    """
    Rewrites the arguments of commands that need their exact text preserved.

    Fire literal-evaluates every argument, so `treefort switch 125` would reach the command as
    an int and `switch 1.10` as a float. Commands declare the positional parameters that must
    stay text, in signature order, in `_literal_args`; their values are rewritten into
    explicitly quoted flags, which fire parses back into the original string. Both positional
    arguments and the flag forms fire accepts (`--name value`, `--name=value`, and the
    single-letter shortcut) are covered, and arguments already given as flags are not filled
    twice. Must run after `replace_alias`, so the `sw` alias is already rewritten to `switch`.

    :param cli: An instance of WorktreeCommands whose commands declare literal arguments
    :return: None
    """
    if len(sys.argv) < 2:
        return
    command = next((command for command in cli._commands if command._name == sys.argv[1]), None)
    if command is None or not command._literal_args:
        return

    pending = list(command._literal_args)
    index = 2
    while index < len(sys.argv):
        token = sys.argv[index]
        if token == "--":
            return
        if _is_flag(token):
            index = _rewrite_flag(sys.argv, index, command._literal_args, pending)
        else:
            if pending:
                sys.argv[index] = f"--{pending.pop(0)}={token!r}"
            index += 1


def replace_verbose():
    """
    Parses command-line arguments to determine if the verbose mode is enabled.

    This function searches arguments before the passthrough separator (`--`) for
    verbose flags (`-v` and `--verbose`). Matching flags are removed from
    `sys.argv`, and verbose mode is considered enabled.

    :return: A boolean indicating whether verbose mode is enabled.
    """
    is_verbose = False

    passthrough_index = sys.argv.index("--") if "--" in sys.argv else len(sys.argv)
    kept_args = [sys.argv[0]]

    for arg in sys.argv[1:passthrough_index]:
        if arg in ("-v", "--verbose"):
            is_verbose = True
            continue
        kept_args.append(arg)

    if passthrough_index < len(sys.argv):
        kept_args.extend(sys.argv[passthrough_index:])

    sys.argv[:] = kept_args
    return is_verbose


def main():
    """CLI tool for managing Git worktrees"""
    is_verbose = replace_verbose()
    component = None

    try:
        component = WorktreeCommands(verbose=is_verbose)
        replace_alias(component)
        replace_literal_args(component)

        # we do not pass `name` here, because the CLI allows using an alias for the command. if name
        # was passed, then using `worktree -- --completion` would use the wrong name for completion
        fire.Fire(component=component)
    except Exception as e:
        runtime = getattr(component, "_runtime", None)
        if is_verbose or getattr(runtime, "verbose", False) is True:
            raise
        logger = getattr(component, "_logger", None) or logging.getLogger(WorktreeCommands._name)
        logger.error(f"Command failed: {str(e)}")
        sys.exit(1)
