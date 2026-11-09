"""Shell integration text, one module per supported shell.

Every script is synthesized from the `CLIManifest` at build time, so nothing here runs at
runtime: `treefort shell-init` only has to look up a pre-rendered string.
"""

from treefort.cli_manifest import CommandSpec
from treefort.shells import bash, fish, zsh
from treefort.shells._common import CMD_PLACEHOLDER

_SHELL_MODULES = {"bash": bash, "zsh": zsh, "fish": fish}
SHELLS = tuple(_SHELL_MODULES)


def render_all_completions(
    commands: dict[str, CommandSpec], alias_map: dict[str, str]
) -> dict[str, str]:
    """Render the completion script for every supported shell.

    :param commands: The commands to complete, keyed by canonical name
    :param alias_map: The alias to canonical command name mapping
    :return: A mapping of shell name to completion script
    """
    return {
        name: module.completion_script(commands, alias_map)
        for name, module in _SHELL_MODULES.items()
    }


def wrapper(shell: str, cmd: str = CMD_PLACEHOLDER) -> str:
    """Render the function that applies the ``TREEFORT_CD_FILE`` directive.

    :param shell: One of `SHELLS`
    :param cmd: The command name the wrapper is installed for
    :return: The shell function definition
    :raises ValueError: When the shell is not supported
    """
    try:
        module = _SHELL_MODULES[shell]
    except KeyError:
        raise ValueError(f"Unknown shell: {shell}") from None
    return module.wrapper(cmd)


__all__ = ["CMD_PLACEHOLDER", "SHELLS", "render_all_completions", "wrapper"]
