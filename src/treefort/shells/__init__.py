"""Shell integration text, one module per supported shell.

Every script is synthesized from the `CLIManifest` at build time, so nothing here runs at
runtime: `treefort shell-init` only has to look up a pre-rendered string.
"""

import os
import re
import sys
from pathlib import Path

from treefort.cli_manifest import CommandSpec
from treefort.shells import bash, fish, zsh
from treefort.shells._common import CMD_PLACEHOLDER

_SHELL_MODULES = {"bash": bash, "zsh": zsh, "fish": fish}
SHELLS = tuple(_SHELL_MODULES)

DEFAULT_CMD_NAME = "treefort"
CMD_NAME_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*")

# Shell keywords and the commands the generated wrappers call. A function may not shadow any of
# these without breaking the wrapper or the shell parsing it.
RESERVED_SHELL_WORDS = frozenset(
    {
        "and",
        "argparse",
        "begin",
        "break",
        "builtin",
        "case",
        "cat",
        "cd",
        "command",
        "continue",
        "coproc",
        "declare",
        "do",
        "done",
        "elif",
        "else",
        "end",
        "esac",
        "eval",
        "exec",
        "exit",
        "export",
        "false",
        "fi",
        "float",
        "for",
        "foreach",
        "function",
        "functions",
        "if",
        "in",
        "integer",
        "local",
        "mktemp",
        "nocorrect",
        "not",
        "or",
        "read",
        "readonly",
        "repeat",
        "return",
        "rm",
        "select",
        "set",
        "shift",
        "source",
        "status",
        "string",
        "switch",
        "test",
        "then",
        "time",
        "trap",
        "true",
        "typeset",
        "unset",
        "until",
        "while",
    }
)


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


def is_valid_command_name(name: str) -> bool:
    """Return whether a name is safe to install as a shell function and ``PATH`` command.

    :param name: The candidate command name
    :return: Whether the name is well formed and not a reserved shell word
    """
    return bool(CMD_NAME_RE.fullmatch(name)) and name not in RESERVED_SHELL_WORDS


def resolve_invoked_command(argv0: str | None = None) -> str:
    """Return the name the running command was invoked as.

    The name comes from ``sys.argv[0]`` without resolving symlinks, so an install under an
    ``--alias`` name registers that alias. Invocations that do not name a command, such as
    ``python -m treefort`` and a direct ``.pex`` run, fall back to `DEFAULT_CMD_NAME`.

    :param argv0: The invoked path, defaulting to ``sys.argv[0]``
    :return: A valid command name
    """
    raw = sys.argv[0] if argv0 is None else argv0
    name = Path(raw).name if raw else ""
    return name if is_valid_command_name(name) else DEFAULT_CMD_NAME


def detect_shell(env_shell: str | None = None) -> str | None:
    """Return the supported shell named by ``$SHELL``.

    :param env_shell: The shell path, defaulting to ``$SHELL``
    :return: One of `SHELLS`, or ``None`` when the shell is unrecognized
    """
    raw = os.environ.get("SHELL", "") if env_shell is None else env_shell
    name = Path(raw).name if raw else ""
    return name if name in SHELLS else None


def init_hook_line(shell: str, cmd: str = DEFAULT_CMD_NAME) -> str:
    """Return the startup file line that loads the shell integration.

    :param shell: One of `SHELLS`
    :param cmd: The command name the hook invokes
    :return: A single shell command line
    :raises ValueError: When the shell or the command name is invalid
    """
    if shell not in SHELLS:
        raise ValueError(f"Unknown shell: {shell}")
    if not is_valid_command_name(cmd):
        raise ValueError(f"Invalid command name: {cmd!r}")
    if shell == "fish":
        return f"{cmd} shell-init fish | source"
    return f'eval "$({cmd} shell-init {shell})"'


def shell_rc_path(shell: str, home: Path | None = None) -> Path:
    """Return the startup file that should load the integration for ``shell``.

    :param shell: One of `SHELLS`
    :param home: The home directory, defaulting to the current user's
    :return: The startup file path
    :raises ValueError: When the shell is not supported
    """
    if shell not in SHELLS:
        raise ValueError(f"Unknown shell: {shell}")
    base_home = Path.home() if home is None else home
    if shell == "zsh":
        zdotdir = os.environ.get("ZDOTDIR")
        return (Path(zdotdir).expanduser() if zdotdir else base_home) / ".zshrc"
    if shell == "bash":
        return _bash_rc_path(base_home)
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg_config_home).expanduser() if xdg_config_home else base_home / ".config"
    return config_home / "fish" / "config.fish"


def _bash_rc_path(base_home: Path) -> Path:
    """Return the bash startup file, preferring the macOS login profile when it is the only one.

    Terminal emulators on macOS start login shells, which read ``.bash_profile`` rather than
    ``.bashrc``. Writing to an existing profile keeps the hook in a file the shell loads.
    """
    bashrc = base_home / ".bashrc"
    bash_profile = base_home / ".bash_profile"
    if sys.platform == "darwin" and not bashrc.exists() and bash_profile.exists():
        return bash_profile
    return bashrc


__all__ = [
    "CMD_NAME_RE",
    "CMD_PLACEHOLDER",
    "DEFAULT_CMD_NAME",
    "RESERVED_SHELL_WORDS",
    "SHELLS",
    "detect_shell",
    "init_hook_line",
    "is_valid_command_name",
    "render_all_completions",
    "resolve_invoked_command",
    "shell_rc_path",
    "wrapper",
]
