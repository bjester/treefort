import fnmatch
import os
import shutil
import string
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from logging import Logger
from pathlib import Path

from treefort import ui
from treefort.context import Context
from treefort.git import TREE_FILE_MODES, GitCLI
from treefort.operator import ConfigOperator
from treefort.utils import normalize_worktree_name, resolve_contained, write_bytes_atomically

GIT_TEMPLATES_DIR = ".treefort/templates"


class TemplateExists(Exception):
    pass


class BaseTemplate(ABC):
    """Strategy for matching and copying template entries into a worktree.

    Subclasses own the copy semantics for a class of template entries, including whether a
    matching directory is copied as a whole unit or traversed by the caller.
    """

    copies_directories: bool = False

    def __init__(self, logger: Logger, patterns: Sequence[str] = ()) -> None:
        """Initialize the template copy strategy.

        :param logger: Logger used for diagnostic messages during template copying.
        :type logger: Logger
        :param patterns: Glob patterns selecting template entries for this strategy.
        :type patterns: Sequence[str]
        """
        self.logger = logger
        self.patterns = tuple(patterns)

    def matches(self, relative_path: Path) -> bool:
        """Check whether a template-relative path matches any pattern for this strategy.

        :param relative_path: Path relative to the templates root directory.
        :type relative_path: Path
        :return: ``True`` if any configured pattern matches ``relative_path``.
        :rtype: bool
        """
        return any(self._matches_pattern(relative_path, pattern) for pattern in self.patterns)

    @staticmethod
    def _matches_pattern(relative_path: Path, pattern: str) -> bool:
        """Check whether a template-relative path matches a single glob pattern.

        :param relative_path: Path relative to the templates root directory.
        :type relative_path: Path
        :param pattern: Glob pattern to test against the path.
        :type pattern: str
        :return: ``True`` if ``pattern`` is non-empty and matches ``relative_path``.
        :rtype: bool
        """
        if not pattern:
            return False
        if fnmatch.fnmatchcase(relative_path.as_posix(), pattern) or fnmatch.fnmatchcase(
            relative_path.name, pattern
        ):
            return True
        try:
            return relative_path.match(pattern)
        except ValueError:
            return False

    @staticmethod
    def _prepare_non_dir_destination(src_path: Path, dest_path: Path) -> None:
        """Prepare a non-directory destination without clobbering symlink targets or directories.

        :param src_path: Source template file or symlink path.
        :type src_path: Path
        :param dest_path: Destination path inside the worktree.
        :type dest_path: Path
        :raises IsADirectoryError: If ``dest_path`` is an existing real directory.
        """
        if dest_path.is_symlink():
            dest_path.unlink()
        elif dest_path.is_dir():
            raise IsADirectoryError(f"Destination path is a directory: {dest_path}")
        if src_path.is_symlink() and dest_path.is_file():
            dest_path.unlink()

    @abstractmethod
    def copy(self, src_path: Path, dest_path: Path) -> None:
        """Copy a template entry from ``src_path`` to ``dest_path``.

        :param src_path: Source template entry path.
        :type src_path: Path
        :param dest_path: Destination path inside the worktree.
        :type dest_path: Path
        """


class RawTemplate(BaseTemplate):
    """Copy a template file or symlink verbatim without variable substitution."""

    def matches(self, relative_path: Path) -> bool:
        """Check whether ``relative_path`` or an ancestor directory matches a raw pattern.

        :param relative_path: Path relative to the templates root directory.
        :type relative_path: Path
        :return: ``True`` if ``relative_path`` or any ancestor directory matches.
        :rtype: bool
        """
        candidates = (relative_path, *list(relative_path.parents)[:-1])
        return any(BaseTemplate.matches(self, candidate) for candidate in candidates)

    def copy(self, src_path: Path, dest_path: Path) -> None:
        """Copy a template file or symlink verbatim without variable substitution.

        :param src_path: Source template file or symlink path.
        :type src_path: Path
        :param dest_path: Destination path inside the worktree.
        :type dest_path: Path
        :raises IsADirectoryError: If ``dest_path`` is an existing real directory.
        """
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare_non_dir_destination(src_path, dest_path)
        shutil.copy2(src_path, dest_path, follow_symlinks=False)


class SubstituteTemplate(BaseTemplate):
    """Copy a text template with ``string.Template`` substitution, falling back to raw."""

    BINARY_SNIFF_BYTES = 8192

    def __init__(
        self,
        logger: Logger,
        replacement_map: Mapping[str, str],
        raw: RawTemplate | None = None,
    ) -> None:
        """Initialize the variable-substitution template strategy.

        :param logger: Logger used for diagnostic messages during template copying.
        :type logger: Logger
        :param replacement_map: Mapping of template variable names to replacement strings.
        :type replacement_map: Mapping[str, str]
        :param raw: Optional ``RawTemplate`` instance used for symlink and binary fallbacks.
        :type raw: RawTemplate | None
        """
        super().__init__(logger)
        self.replacement_map = replacement_map
        self._raw = raw if raw is not None else RawTemplate(logger)

    def matches(self, relative_path: Path) -> bool:
        """Match any template entry as the default catch-all strategy.

        :param relative_path: Path relative to the templates root directory.
        :type relative_path: Path
        :return: Always ``True``.
        :rtype: bool
        """
        return True

    def copy(self, src_path: Path, dest_path: Path) -> None:
        """Copy a template file with ``string.Template`` substitution, falling back to raw.

        Symlinks, files containing NUL bytes in their first ``BINARY_SNIFF_BYTES`` bytes, and
        non-UTF-8 files automatically delegate to ``self._raw.copy``.

        :param src_path: Source template file or symlink path.
        :type src_path: Path
        :param dest_path: Destination path inside the worktree.
        :type dest_path: Path
        :raises IsADirectoryError: If ``dest_path`` is an existing real directory.
        """
        if src_path.is_symlink():
            self._raw.copy(src_path, dest_path)
            return

        with src_path.open("rb") as src:
            chunk = src.read(self.BINARY_SNIFF_BYTES)
        if b"\x00" in chunk:
            self.logger.debug(f"Detected binary template {src_path.name}; copying raw")
            self._raw.copy(src_path, dest_path)
            return

        try:
            content = src_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self.logger.debug(f"Detected non-UTF-8 template {src_path.name}; copying raw")
            self._raw.copy(src_path, dest_path)
            return

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare_non_dir_destination(src_path, dest_path)
        dest_path.write_text(
            string.Template(content).safe_substitute(self.replacement_map),
            encoding="utf-8",
        )
        dest_path.chmod(src_path.stat().st_mode)


class CowTemplate(BaseTemplate):
    """Copy a template file, symlink, or directory using OS Copy-on-Write reflinks.

    Matching directories are copied as one subtree, so nested symlinks are copied as links
    without the ``templates_dir`` containment check that ``Templates._walk_templates``
    applies to the entries it visits.
    """

    copies_directories: bool = True

    @staticmethod
    def _build_command(src_path: Path, dest_path: Path) -> list[str]:
        """Build the platform-specific ``cp`` command for Copy-on-Write cloning.

        :param src_path: Source template file, symlink, or directory path.
        :type src_path: Path
        :param dest_path: Destination path inside the worktree.
        :type dest_path: Path
        :return: Subprocess argument vector with ``--`` separating options from paths.
        :rtype: list[str]
        """
        if sys.platform == "darwin":
            return ["cp", "-Rc", "-p", "--", str(src_path), str(dest_path)]
        return [
            "cp",
            "-R",
            "--reflink=auto",
            "--preserve=mode,timestamps",
            "--",
            str(src_path),
            str(dest_path),
        ]

    def _merge_directory(self, src_dir: Path, dest_dir: Path) -> None:
        """Recursively merge a source directory into an existing destination directory.

        Preserves symlinks, unlinks overwritten destination symlinks so targets are never
        clobbered, and warns and skips when a destination directory path is a symlink.

        :param src_dir: Source template directory to merge from.
        :type src_dir: Path
        :param dest_dir: Destination directory inside the worktree to merge into.
        :type dest_dir: Path
        :raises FileExistsError: If ``dest_dir`` is an existing non-directory file.
        :raises IsADirectoryError: If a non-directory entry collides with an existing directory.
        """
        if dest_dir.is_symlink():
            self.logger.warning(f"Skipping template directory {dest_dir}: destination is a symlink")
            return
        dest_dir.mkdir(parents=True, exist_ok=True)
        for entry in sorted(src_dir.iterdir()):
            target = dest_dir / entry.name
            if not entry.is_symlink() and entry.is_dir():
                self._merge_directory(entry, target)
                continue
            self._prepare_non_dir_destination(entry, target)
            shutil.copy2(entry, target, follow_symlinks=False)
        shutil.copystat(src_dir, dest_dir)

    @classmethod
    def _copy_dir_fallback(cls, src_path: Path, dest_path: Path) -> None:
        """Copy a COW directory with ``shutil.copytree``, cleaning any partial ``cp`` output.

        :param src_path: Source template directory path.
        :type src_path: Path
        :param dest_path: Destination directory path inside the worktree.
        :type dest_path: Path
        """
        if not dest_path.is_symlink() and dest_path.is_dir():
            shutil.rmtree(dest_path)
        shutil.copytree(
            src_path,
            dest_path,
            copy_function=shutil.copy2,
            dirs_exist_ok=True,
            symlinks=True,
        )

    @classmethod
    def _copy_non_dir_fallback(cls, src_path: Path, dest_path: Path) -> None:
        """Copy a COW file or symlink with ``shutil.copy2`` without following symlinks.

        :param src_path: Source template file or symlink path.
        :type src_path: Path
        :param dest_path: Destination path inside the worktree.
        :type dest_path: Path
        """
        cls._prepare_non_dir_destination(src_path, dest_path)
        shutil.copy2(src_path, dest_path, follow_symlinks=False)

    def copy(self, src_path: Path, dest_path: Path) -> None:
        """Copy a template file, symlink, or directory using OS Copy-on-Write reflinks.

        Merges into an existing target directory via ``_merge_directory``, and falls back to
        ``shutil`` if ``cp`` is unavailable or exits with an error.

        :param src_path: Source template file, symlink, or directory path.
        :type src_path: Path
        :param dest_path: Destination path inside the worktree.
        :type dest_path: Path
        :raises FileExistsError: If a directory source targets an existing non-directory file.
        :raises IsADirectoryError: If a non-directory source targets an existing real directory.
        """
        is_real_dir = not src_path.is_symlink() and src_path.is_dir()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if is_real_dir and (dest_path.exists() or dest_path.is_symlink()):
            self._merge_directory(src_path, dest_path)
            return

        if not is_real_dir:
            self._prepare_non_dir_destination(src_path, dest_path)

        fallback = self._copy_dir_fallback if is_real_dir else self._copy_non_dir_fallback
        if shutil.which("cp") is None:
            self.logger.debug(f"cp not available; falling back to shutil copy for {src_path}")
            fallback(src_path, dest_path)
            return

        cmd = self._build_command(src_path, dest_path)
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            stderr = (
                exc.stderr.strip()
                if isinstance(exc, subprocess.CalledProcessError) and exc.stderr
                else ""
            )
            detail = stderr or str(exc)
            self.logger.debug(
                f"Copy-on-write cp failed for {src_path} ({detail}); falling back to shutil copy"
            )
            fallback(src_path, dest_path)


@dataclass(frozen=True, slots=True)
class _TemplateWalkContext:
    """Immutable traversal state shared across recursive template directory walks."""

    templates_dir: Path
    templates_root: Path
    worktree_dir: Path
    handlers: tuple[BaseTemplate, ...]

    def resolve_handler(self, relative_path: Path) -> BaseTemplate:
        """Return the first template handler matching ``relative_path``.

        :param relative_path: Path relative to the templates root directory.
        :type relative_path: Path
        :return: First matching template handler.
        :rtype: BaseTemplate
        :raises LookupError: If no configured handler matches ``relative_path``.
        """
        for handler in self.handlers:
            if handler.matches(relative_path):
                return handler
        raise LookupError(f"No template handler matched {relative_path}")


class Templates(ConfigOperator):
    """
    Copies files from /templates directory into a worktree, replacing environment variables in the
    process.
    """

    def __init__(self, context: Context, logger: Logger):
        super().__init__(context, logger)
        self.dir_name = "templates"
        self.replacement_map = {}

        global_config = context.get_global_config()
        for envvar_name in global_config.allowed_envvars:
            self.replacement_map[envvar_name] = os.environ.get(envvar_name, "")

    def copy(self, worktree_name: str):
        """Copy template entries from global and project templates directories into a worktree.

        :param worktree_name: The name of the worktree to copy the templates into.
        :type worktree_name: str
        """
        global_config = self.context.get_global_config()
        config = self.context.get_config()
        worktree_dir = self.context.project_dir / worktree_name
        values = {
            "REPO_NAME": config.name,
            "REPO_DIR": str(self.context.project_dir),
            "DEFAULT_BRANCH": getattr(config, "default_branch", "main") or "main",
            "WORKTREE_NAME": worktree_name,
            "WORKTREE_NAME_NORMALIZED": normalize_worktree_name(worktree_name),
            "WORKTREE_DIR": str(worktree_dir),
        }
        self.replacement_map.update(values)
        # Mirror every key with the TREEFORT_ prefix a hook sees in its environment, so one name
        # works in both a template and a hook script. Deriving the mirrors from the same mapping
        # keeps them from drifting apart.
        self.replacement_map.update({f"TREEFORT_{key}": value for key, value in values.items()})

        handlers = self._build_handlers(global_config, config)

        copied_count = 0
        for templates_dir in self.iter_config_dirs():
            walk_ctx = _TemplateWalkContext(
                templates_dir=templates_dir,
                templates_root=templates_dir.resolve(),
                worktree_dir=worktree_dir,
                handlers=handlers,
            )
            copied_count += self._walk_templates(templates_dir, walk_ctx)

        if copied_count:
            self.logger.info(ui.success(f"Copied {copied_count} template(s)"))

    def _build_handlers(self, global_config: object, config: object) -> tuple[BaseTemplate, ...]:
        """Build ordered template copy handlers from global and project configuration.

        :param global_config: Global configuration object providing template patterns.
        :type global_config: object
        :param config: Project configuration object providing template patterns.
        :type config: object
        :return: Ordered handlers evaluated by precedence (COW, raw, substitute).
        :rtype: tuple[BaseTemplate, ...]
        """
        cow_patterns = [
            *(getattr(global_config, "template_cow_patterns", []) or []),
            *(getattr(config, "template_cow_patterns", []) or []),
        ]
        raw_patterns = [
            *(getattr(global_config, "template_raw_patterns", []) or []),
            *(getattr(config, "template_raw_patterns", []) or []),
        ]
        raw_template = RawTemplate(self.logger, patterns=raw_patterns)
        return (
            CowTemplate(self.logger, patterns=cow_patterns),
            raw_template,
            SubstituteTemplate(self.logger, self.replacement_map, raw=raw_template),
        )

    @staticmethod
    def _is_safe_template_path(path: Path, templates_root: Path) -> bool:
        """Verify that a template entry does not escape ``templates_root`` via symlink.

        :param path: Candidate template entry path to validate.
        :type path: Path
        :param templates_root: Root templates directory that symlink targets must stay inside.
        :type templates_root: Path
        :return: ``True`` if ``path`` is not a symlink or resolves within ``templates_root``.
        :rtype: bool
        """
        if not path.is_symlink():
            return True
        try:
            # Resolve with strict=True first so circular symlinks raise OSError(ELOOP) on
            # Python 3.13+, where strict=False stops traversing loops and returns the link path.
            try:
                resolved_path = path.resolve(strict=True)
            except FileNotFoundError:
                resolved_path = path.resolve(strict=False)
            resolved_root = templates_root.resolve()
        except (OSError, RuntimeError):
            return False
        return resolved_path.is_relative_to(resolved_root)

    def _walk_templates(self, current_dir: Path, walk_ctx: _TemplateWalkContext) -> int:
        """Recursively copy template entries top-down, returning the number of copied items.

        :param current_dir: Template directory currently being traversed.
        :type current_dir: Path
        :param walk_ctx: Shared traversal configuration and root directories.
        :type walk_ctx: _TemplateWalkContext
        :return: Count of files, valid internal symlinks, and directory-copying entries copied
            beneath ``current_dir``.
        :rtype: int
        """
        copied_count = 0
        for entry_path in sorted(current_dir.iterdir()):
            if not self._is_safe_template_path(entry_path, walk_ctx.templates_root):
                self.logger.warning(
                    f"Skipping template symlink outside templates directory: {entry_path}"
                )
                continue

            relative_path = entry_path.relative_to(walk_ctx.templates_dir)
            dest_path = walk_ctx.worktree_dir / relative_path
            handler = walk_ctx.resolve_handler(relative_path)
            is_real_dir = not entry_path.is_symlink() and entry_path.is_dir()
            if is_real_dir and dest_path.is_symlink():
                self.logger.warning(
                    f"Skipping template directory {relative_path}: destination is a symlink"
                )
                continue
            if is_real_dir and not handler.copies_directories:
                dest_path.mkdir(parents=True, exist_ok=True)
                copied_count += self._walk_templates(entry_path, walk_ctx)
                continue

            self.logger.debug(f"Copying template {relative_path}")
            handler.copy(entry_path, dest_path)
            copied_count += 1

        return copied_count

    @contextmanager
    def add(self, relative_path: str):
        template_file = self.context.project_dir / relative_path
        template_file.parent.mkdir(parents=True, exist_ok=True)

        if template_file.exists():
            raise TemplateExists(f"Template {relative_path} already exists.")

        # copy it to config
        with template_file.open("w", encoding="utf-8") as f:
            yield f

    def sync_from_git(self, git: GitCLI, ref: str, overwrite: bool = True) -> int:
        """
        Synchronize the project templates tracked in a Git ref into the configuration directory.

        Blobs are written undecoded, so binary templates survive byte for byte, and the mode
        recorded in the Git tree decides whether the file is executable.
        :param git: The Git CLI used to read the tree entries and blobs
        :param ref: The Git ref to read the templates from
        :param overwrite: Whether to replace templates that already exist on disk
        :return: The number of templates written
        """
        templates_dir = self.context.config_dir / self.dir_name
        synced = 0

        for entry in git.ls_tree_entries(ref, GIT_TEMPLATES_DIR):
            if entry.obj_type != "blob" or entry.mode not in TREE_FILE_MODES:
                continue
            if not entry.path.startswith(f"{GIT_TEMPLATES_DIR}/"):
                continue

            rel_path = entry.path.removeprefix(f"{GIT_TEMPLATES_DIR}/")
            dest = resolve_contained(templates_dir, rel_path)
            if not overwrite and dest.exists():
                self.logger.warning(f"Template {dest} already exists. Skipping.")
                continue

            mode = 0o755 if entry.is_executable else 0o644
            write_bytes_atomically(dest, git.read_blob(entry.sha), mode)
            synced += 1

        return synced
