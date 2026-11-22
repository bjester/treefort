import sys
from typing import Literal

from treefort.cli_manifest import load_manifest
from treefort.command import BaseCommand, command
from treefort.errors import ValidationError
from treefort.shells import CMD_PLACEHOLDER, SHELLS, detect_shell, resolve_invoked_command, wrapper


@command("shell-init")
class ShellInitCommand(BaseCommand):
    """Emit the shell wrapper and tab-completion script for a supported shell"""

    # Answering from the generated manifest alone keeps shell startup free of disk writes,
    # configuration parsing, and migrations, and lets it work inside a deleted directory
    _requires_runtime = False

    def __call__(
        self,
        shell: Literal["bash", "zsh", "fish"] | None = None,
    ) -> None:
        """Emit the shell wrapper and tab-completion script for bash, zsh, or fish.

        Infers the command name from the invoked executable and auto-detects the current
        shell from `$SHELL` when `shell` is omitted. Prefer `treefort install --shell` to
        register this hook automatically in your shell startup file.

        :param shell: Target shell (`bash`, `zsh`, or `fish`), auto-detected from `$SHELL` when
            omitted
        """
        resolved_shell = self._resolve_shell(shell)
        cmd = resolve_invoked_command()
        wrapper_script = wrapper(resolved_shell, cmd)
        completion_script = (
            load_manifest().shell_completions[resolved_shell].replace(CMD_PLACEHOLDER, cmd)
        )
        sys.stdout.write(f"{wrapper_script}\n\n{completion_script}")

    def _resolve_shell(self, shell: str | None) -> str:
        """Return the shell to emit, resolving ``$SHELL`` when none was given."""
        if shell is None:
            detected = detect_shell()
            if detected is None:
                raise ValidationError(
                    "Could not detect a supported shell from $SHELL. Specify bash, zsh, or fish."
                )
            return detected
        if shell not in SHELLS:
            raise ValidationError(f"Unsupported shell: {shell!r}")
        return shell
