"""Shared inputs for the shell integrations that wrap fire's completion templates."""

# Mirrors the `-v`/`--verbose` flags that treefort.cli strips before dispatch. New root-wide
# flags must be added here as well, or they disappear from tab completion.
#
# Completion invariant, encoded in every shell snippet: the subcommand is the FIRST non-flag
# token at index >= 1, and --remote is the only flag that consumes the following token. A
# new root-level flag that takes a value invalidates the subcommand detection everywhere.
DEFAULT_OPTIONS = frozenset({"--verbose", "-v"})
