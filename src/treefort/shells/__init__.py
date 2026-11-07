"""Shell integration scripts: one module per supported shell.

Each module exposes ``wrapper(cmd)``, the function that performs requested directory changes
in the shell, and ``completion_script(cmd, commands, hook_names)``, the patched fire tab
completion. The registry below is the single source of truth for the supported shells.
"""

from treefort.shells import bash, fish, zsh

SHELL_MODULES = {"bash": bash, "zsh": zsh, "fish": fish}
SHELLS = tuple(SHELL_MODULES)
