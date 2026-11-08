import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PACKAGE_DIR = Path(__file__).resolve().parent
GENERATED_MODULE_NAME = "_cli_generated.py"


class ManifestUnavailable(RuntimeError):
    """Raised when the generated CLI manifest is missing and cannot be rebuilt."""


@dataclass(frozen=True)
class ParamSpec:
    """A single CLI parameter of a command."""

    name: str
    cli_name: str
    kind: Literal["positional", "variadic_positional", "flag"]
    value_type: Literal["str", "bool", "int"]
    nullable: bool
    required: bool
    has_default: bool
    default: str | bool | int | None
    choices: tuple[str, ...]
    short: str | None
    completion: str
    description: str


@dataclass(frozen=True)
class CommandSpec:
    """A single CLI command, its parameters, and its pre-rendered help text."""

    name: str
    aliases: tuple[str, ...]
    summary: str
    description: str
    params: tuple[ParamSpec, ...]
    help_text: str


@dataclass(frozen=True)
class CLIManifest:
    """Every command, alias, help text, and completion script the CLI needs."""

    prog_name: str
    summary: str
    description: str
    commands: dict[str, CommandSpec]
    alias_map: dict[str, str]
    root_help_text: str
    shell_completions: dict[str, str]


def max_source_mtime_ns(package_dir: Path | None = None) -> int:
    """Return the newest modification time across the package's Python sources.

    The generated module is excluded so writing it never makes the manifest look stale.

    :param package_dir: The package directory to scan, defaulting to this package
    :return: The newest ``st_mtime_ns``, or ``0`` when there are no sources. A path that holds
        no directory, such as a module inside a zip archive, also yields ``0`` so a shipped
        generated manifest stays authoritative.
    """
    directory = PACKAGE_DIR if package_dir is None else package_dir
    newest = 0
    for path in directory.rglob("*.py"):
        if path.name == GENERATED_MODULE_NAME:
            continue
        try:
            newest = max(newest, path.stat().st_mtime_ns)
        except OSError:
            # A dangling symlink or an unreadable file should not break CLI startup
            continue
    return newest


def is_packaged() -> bool:
    """Return whether treefort runs from a built artifact rather than a source checkout.

    Built artifacts ship the generated manifest, so it must be used as-is: the source files it
    would be compared against are either absent or immutable.
    """
    if getattr(sys, "frozen", False):
        return True

    location = str(Path(__file__).resolve())
    if ".pex" in location or "site-packages" in location or "dist-packages" in location:
        return True

    # An extracted PEX puts its modules in a temporary directory
    return any(entry.endswith(".pex") for entry in sys.path)


def extract_synopsis(help_text: str) -> str:
    """Return the ``SYNOPSIS`` line from pre-rendered help text.

    :param help_text: Rendered help text
    :return: The synopsis line, or an empty string when there is none
    """
    lines = help_text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "SYNOPSIS":
            continue
        for candidate in lines[index + 1 :]:
            if candidate.strip():
                return candidate.strip()
        break
    return ""


def load_manifest() -> CLIManifest:
    """Return the CLI manifest, rebuilding it only for a stale or unbuilt source checkout."""
    try:
        from treefort import _cli_generated
    except ImportError as error:
        # An absent generated module names either this package or the module itself. Any other
        # import failure means the module exists but is broken, and must not be masked by a
        # rebuild that would hide the real error.
        if getattr(error, "name", None) not in ("treefort", "treefort._cli_generated"):
            raise
        if not _can_rebuild():
            raise ManifestUnavailable(
                "treefort is missing its generated CLI manifest; reinstall it, or run "
                "'make codegen' in a source checkout"
            ) from None
        return _build_manifest()

    if (
        is_packaged()
        or not _can_rebuild()
        or _cli_generated._SOURCE_MTIME_NS >= max_source_mtime_ns()
    ):
        return _cli_generated.MANIFEST

    return _build_manifest()


def _can_rebuild() -> bool:
    """Return whether the generator is available to rebuild the manifest.

    Built artifacts exclude the generator, and a PEX or wheel can put the package anywhere, so
    the generated manifest is authoritative whenever a rebuild is impossible.
    """
    try:
        return importlib.util.find_spec("treefort.cli_codegen") is not None
    except (ImportError, ValueError):
        return False


def _build_manifest() -> CLIManifest:
    # Imported lazily so built artifacts can omit the generator entirely
    from treefort.cli_codegen import build_manifest

    return build_manifest()
