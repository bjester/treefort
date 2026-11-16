"""Zsh completion script synthesis.

Zsh's `bashcompinit` runs bash completion functions, so the completion script is the bash one
with the compatibility shim prepended.
"""

from treefort.cli_manifest import CommandSpec
from treefort.shells import bash
from treefort.shells._common import CMD_PLACEHOLDER

SHIM = "autoload -Uz bashcompinit && bashcompinit"


def wrapper(cmd: str = CMD_PLACEHOLDER) -> str:
    """Render the zsh function that applies the ``TREEFORT_CD_FILE`` directive."""
    return bash.wrapper(cmd)


def completion_script(
    commands: dict[str, CommandSpec], alias_map: dict[str, str], cmd: str = CMD_PLACEHOLDER
) -> str:
    """Render a zsh completion script for the given commands."""
    return f"{SHIM}\n\n{bash.completion_script(commands, alias_map, cmd)}"
