"""Shared inputs and patches for the shell integrations that wrap fire's completion templates."""

import re

from treefort.errors import CompletionTemplateError

# Mirrors the `-v`/`--verbose` flags that treefort.cli strips before dispatch. New root-wide
# flags must be added here as well, or they disappear from tab completion.
#
# Completion invariant, encoded in every shell snippet: the subcommand is the FIRST non-flag
# token at index >= 1, and --remote is the only flag that consumes the following token. A
# new root-level flag that takes a value invalidates the subcommand detection everywhere.
DEFAULT_OPTIONS = frozenset({"--verbose", "-v"})


def replace_required(script: str, old: str, new: str, count: int = -1) -> str:
    """Replace text in fire's completion template, failing when the anchor is gone.

    A silent no-op would ship a script with the dynamic completion or a fish fix quietly
    missing. A crash at `shell-init` time, which is usually shell profile load, is easier to
    diagnose than completion that degrades without a trace.
    """
    if old not in script:
        raise CompletionTemplateError(f"fire completion template changed; expected {old!r}")
    return script.replace(old, new, count)


def replace_required_regex(script: str, pattern: str, new: str) -> str:
    """Replace a pattern in fire's completion template, failing when it does not match."""
    if re.search(pattern, script, flags=re.DOTALL) is None:
        raise CompletionTemplateError(f"fire completion template changed; expected {pattern!r}")
    return re.sub(pattern, new, script, count=1, flags=re.DOTALL)
