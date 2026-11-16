import logging
import sys

from treefort import ui
from treefort.cli_manifest import (
    CLIManifest,
    ManifestUnavailable,
    extract_synopsis,
    load_manifest,
)
from treefort.cli_parser import parse_cli_args
from treefort.errors import CLIUsageError
from treefort.main import WorktreeCommands


def _help_text(manifest: CLIManifest, command_name: str | None) -> str:
    """Return the pre-rendered help text for a command, or for the program."""
    if command_name is None:
        return manifest.root_help_text
    return manifest.commands[command_name].help_text


def _usage_hint(manifest: CLIManifest, command_name: str | None) -> str:
    """Return the command a user should run to see usage."""
    if command_name is None:
        return f"{manifest.prog_name} --help"
    return f"{manifest.prog_name} {command_name} --help"


def _report_usage_error(manifest: CLIManifest, error: CLIUsageError) -> None:
    """Write a usage error, the relevant synopsis, and the hint to show usage."""
    synopsis = extract_synopsis(_help_text(manifest, error.command_name))
    sys.stderr.write(f"{ui.red(f'✗ {error}', sys.stderr)}\n")
    if synopsis:
        sys.stderr.write(f"{synopsis}\n")
    sys.stderr.write(f"Run '{_usage_hint(manifest, error.command_name)}' for usage.\n")


def main():
    """CLI tool for managing Git worktrees"""
    try:
        manifest = load_manifest()
    except ManifestUnavailable as error:
        sys.exit(f"treefort: {error}")

    try:
        parsed = parse_cli_args(manifest, sys.argv[1:])
    except CLIUsageError as error:
        _report_usage_error(manifest, error)
        sys.exit(2)

    # Help, and the impossible case of no command without a help request, are answered from
    # the manifest alone, before any runtime side effects
    if parsed.help_requested or parsed.command_name is None:
        sys.stdout.write(_help_text(manifest, parsed.command_name))
        return

    component = None
    try:
        component = WorktreeCommands(verbose=parsed.verbose)
        getattr(component, parsed.command_name)(*parsed.args, **parsed.kwargs)
    except Exception as e:
        runtime = getattr(component, "_runtime", None)
        if parsed.verbose or getattr(runtime, "verbose", False) is True:
            raise
        logger = getattr(component, "_logger", None) or logging.getLogger(WorktreeCommands._name)
        logger.error(f"Command failed: {str(e)}")
        sys.exit(1)
