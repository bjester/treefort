"""Zsh shell integration: bashcompinit plus the bash wrapper and completion script."""

from treefort.shells import bash


def wrapper(cmd: str) -> str:
    """The wrapper is the bash function, which zsh runs unchanged."""
    return bash.wrapper(cmd)


def completion_script(cmd: str, commands: list[tuple[str, ...]], hook_names: str) -> str:
    """Fire's bash completion, run through zsh's bashcompinit."""
    script = bash.completion_script(cmd, commands, hook_names)
    return f"autoload -Uz bashcompinit && bashcompinit\n\n{script}"
