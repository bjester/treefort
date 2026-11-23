import re
import tempfile
from pathlib import Path

from treefort.errors import AncestorNotFoundError, ValidationError


def find_up(name: str, start_path: str | Path) -> Path:
    """
    Looks upward for a directory that has file or directory with `name`
    :param name: The name of the file or directory to look for
    :param start_path: The path to start looking from
    :return: The path to the directory
    """
    search_path = Path(start_path).resolve()

    while True:
        name_path = search_path / name
        if search_path.is_dir() and name_path.exists():
            return name_path
        if search_path == search_path.parent:
            break
        search_path = search_path.parent

    raise AncestorNotFoundError(f"Could not find {name} in {start_path} ancestors")


def normalize_worktree_name(name: str) -> str:
    """
    Normalize a worktree name by replacing slashes and other non-alphanumeric
    characters with dashes, keeping only letters, numbers, and dashes.
    Consecutive replacement characters are collapsed into a single dash.
    :param name: The worktree name to normalize
    :return: A normalized version of the worktree name
    """
    # Replace any non-alphanumeric character (except dash) with a dash
    result = re.sub(r"[^a-zA-Z0-9-]", "-", name)
    # Collapse consecutive dashes into a single dash
    result = re.sub(r"-+", "-", result)
    return result


def write_bytes_atomically(path: Path, content: bytes, mode: int) -> None:
    """
    Write bytes to a sibling temporary file, then replace the destination with it.

    The destination keeps its previous content when the write fails, so an interrupted
    sync never leaves a partially written hook or template behind.
    :param path: The destination file, whose parent directories are created as needed
    :param content: The bytes to write
    :param mode: The POSIX permission bits to apply before the replacement
    :raises ValidationError: When a local file or directory blocks the destination
    """
    temp_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)
        temp_path.chmod(mode)
        temp_path.replace(path)
    except (FileExistsError, IsADirectoryError, NotADirectoryError) as e:
        raise ValidationError(
            f"Cannot write {path}: a local file or directory is in the way"
        ) from e
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def resolve_contained(base_dir: Path, rel_path: str) -> Path:
    """
    Resolve a path relative to a base directory, refusing any path that escapes it.

    Nested paths are allowed, so a template may live in a subdirectory. A path that is
    empty, names the base directory itself, or resolves outside it is rejected.
    :param base_dir: The directory the destination must stay inside
    :param rel_path: The path relative to the base directory
    :return: The destination path
    :raises ValidationError: When the path does not name a file inside the base directory
    """
    resolved_dir = base_dir.resolve()
    dest = base_dir / rel_path
    resolved_dest = dest.resolve()
    if (
        not rel_path
        or resolved_dest == resolved_dir
        or not resolved_dest.is_relative_to(resolved_dir)
    ):
        raise ValidationError(f"Path escapes {base_dir}: {rel_path!r}")
    return dest
