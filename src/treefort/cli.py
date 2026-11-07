import logging
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
