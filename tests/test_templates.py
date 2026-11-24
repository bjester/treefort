import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from treefort.config import (
    AllowedHooksConfig,
    GlobalConfig,
    JsonConfigIO,
    RepositoryConfig,
    TomlConfigIO,
)
from treefort.errors import ConfigTypeError, ValidationError
from treefort.git import GitTreeEntry
from treefort.templates import (
    CowTemplate,
    RawTemplate,
    SubstituteTemplate,
    TemplateExists,
    Templates,
    _TemplateWalkContext,
)


class StubContext:
    def __init__(self, project_dir, config, global_config, config_dir, global_config_dir):
        self.project_dir = project_dir
        self._config = config
        self._global_config = global_config
        self.config_dir = config_dir
        self.global_config_dir = global_config_dir

    def get_config(self):
        return self._config

    def get_global_config(self):
        return self._global_config


class RawTemplateTestCase(unittest.TestCase):
    """Test case for the RawTemplate strategy."""

    def setUp(self):
        """Set up temporary source and destination directories."""
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.src_dir = self.tmp_path / "src"
        self.dest_dir = self.tmp_path / "dest"
        self.src_dir.mkdir()
        self.dest_dir.mkdir()
        self.logger = Mock()

    def tearDown(self):
        """Clean up temporary directories."""
        self.tmp_dir.cleanup()

    def test_copies_directories__is_false(self):
        """Test that RawTemplate does not claim whole directories."""
        self.assertFalse(RawTemplate(self.logger).copies_directories)

    def test_matches__matches_filename_posix_and_relative_path(self):
        """Test that matches supports filename, posix path, and nested relative globs."""
        template = RawTemplate(self.logger, patterns=["*.png", "scripts/*.sh", "subdir/*.txt"])

        self.assertTrue(template.matches(Path("assets/logo.png")))
        self.assertTrue(template.matches(Path("scripts/deploy.sh")))
        # Exercises fnmatchcase on posix_path independently of Path.match (where * stops at /).
        self.assertTrue(template.matches(Path("scripts/sub/deploy.sh")))
        self.assertTrue(template.matches(Path("deep/subdir/notes.txt")))
        self.assertFalse(template.matches(Path("config.txt")))

    def test_matches__is_case_sensitive(self):
        """Test that pattern matching respects filename and path case."""
        template = RawTemplate(self.logger, patterns=["*.PNG"])

        self.assertFalse(template.matches(Path("assets/logo.png")))

    def test_matches__ignores_empty_and_degenerate_patterns(self):
        """Test that empty or degenerate patterns are ignored without raising ValueError."""
        empty_only = RawTemplate(self.logger, patterns=["", ".", "./"])
        mixed = RawTemplate(self.logger, patterns=["", ".", "scripts/*.sh"])

        self.assertFalse(empty_only.matches(Path("config.txt")))
        self.assertTrue(mixed.matches(Path("scripts/run.sh")))

    def test_copy__copies_file_verbatim_and_preserves_permissions(self):
        """Test that copy writes files verbatim without variable substitution and keeps mode."""
        src_file = self.src_dir / "deploy.sh"
        src_file.write_text("#!/bin/sh\necho ${REPO_NAME} $WORKTREE_NAME\n")
        src_file.chmod(0o755)
        dest_file = self.dest_dir / "scripts" / "deploy.sh"

        RawTemplate(self.logger).copy(src_file, dest_file)

        self.assertEqual(dest_file.read_text(), "#!/bin/sh\necho ${REPO_NAME} $WORKTREE_NAME\n")
        self.assertEqual(dest_file.stat().st_mode & 0o777, 0o755)

    def test_copy__preserves_symlinks_and_overwrites_regular_file(self):
        """Test that copy preserves symlinks and safely replaces existing regular files."""
        (self.src_dir / "target.txt").write_text("TARGET\n")
        src_link = self.src_dir / "link.txt"
        src_link.symlink_to("target.txt")

        dest_link = self.dest_dir / "nested" / "link.txt"
        RawTemplate(self.logger).copy(src_link, dest_link)
        self.assertTrue(dest_link.is_symlink())
        self.assertEqual(dest_link.readlink(), Path("target.txt"))

        existing_file = self.dest_dir / "was_regular.txt"
        existing_file.write_text("OLD\n")
        RawTemplate(self.logger).copy(src_link, existing_file)
        self.assertTrue(existing_file.is_symlink())
        self.assertEqual(existing_file.readlink(), Path("target.txt"))

    def test_copy__unlinks_destination_symlink_without_clobbering_target(self):
        """Test that copy unlinks an existing destination symlink before copying a file."""
        target = self.dest_dir / "target.txt"
        target.write_text("ORIGINAL\n")
        dest_symlink = self.dest_dir / "override.bin"
        dest_symlink.symlink_to(target)

        src_file = self.src_dir / "override.bin"
        src_file.write_text("RAW: $REPO_NAME\n")

        RawTemplate(self.logger).copy(src_file, dest_symlink)

        self.assertFalse(dest_symlink.is_symlink())
        self.assertEqual(dest_symlink.read_text(), "RAW: $REPO_NAME\n")
        self.assertEqual(target.read_text(), "ORIGINAL\n")

    def test_copy__raises_when_destination_is_existing_directory(self):
        """Test that copy fails fast with IsADirectoryError when dest is a real directory."""
        existing_file_dir = self.dest_dir / "collision.bin"
        existing_file_dir.mkdir()
        existing_link_dir = self.dest_dir / "collision_link"
        existing_link_dir.mkdir()

        raw_file = self.src_dir / "collision.bin"
        raw_file.write_bytes(b"RAW DATA")
        internal_link = self.src_dir / "collision_link"
        internal_link.symlink_to("collision.bin")

        template = RawTemplate(self.logger)

        with self.assertRaises(IsADirectoryError):
            template.copy(raw_file, existing_file_dir)
        self.assertEqual(list(existing_file_dir.iterdir()), [])

        with self.assertRaises(IsADirectoryError):
            template.copy(internal_link, existing_link_dir)
        self.assertEqual(list(existing_link_dir.iterdir()), [])


class SubstituteTemplateTestCase(unittest.TestCase):
    """Test case for the SubstituteTemplate strategy."""

    def setUp(self):
        """Set up temporary source and destination directories."""
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.src_dir = self.tmp_path / "src"
        self.dest_dir = self.tmp_path / "dest"
        self.src_dir.mkdir()
        self.dest_dir.mkdir()
        self.logger = Mock()
        self.replacement_map = {"REPO_NAME": "test-repo", "WORKTREE_NAME": "my-worktree"}

    def tearDown(self):
        """Clean up temporary directories."""
        self.tmp_dir.cleanup()

    def test_copies_directories__is_false(self):
        """Test that SubstituteTemplate does not claim whole directories."""
        template = SubstituteTemplate(self.logger, self.replacement_map)

        self.assertFalse(template.copies_directories)

    def test_matches__always_returns_true(self):
        """Test that SubstituteTemplate acts as a catch-all strategy."""
        template = SubstituteTemplate(self.logger, self.replacement_map)

        self.assertTrue(template.matches(Path("config.txt")))
        self.assertTrue(template.matches(Path("deep/nested/file.bin")))

    def test_copy__substitutes_variables_keeps_undefined_and_preserves_permissions(self):
        """Test that copy substitutes known vars, preserves undefined vars, and keeps file mode."""
        src_file = self.src_dir / "script.sh"
        src_file.write_text("#!/bin/sh\necho $REPO_NAME $WORKTREE_NAME $MISSING_VAR\n")
        src_file.chmod(0o755)
        dest_file = self.dest_dir / "nested" / "script.sh"

        SubstituteTemplate(self.logger, self.replacement_map).copy(src_file, dest_file)

        self.assertEqual(
            dest_file.read_text(),
            "#!/bin/sh\necho test-repo my-worktree $MISSING_VAR\n",
        )
        self.assertEqual(dest_file.stat().st_mode & 0o777, 0o755)

    def test_copy__falls_back_to_raw_for_binary_file_with_null_bytes(self):
        """Test that valid-UTF-8 payloads containing NUL bytes trigger binary raw fallback."""
        payload = b"SQLite format 3\x00$REPO_NAME\n"
        src_file = self.src_dir / "app.sqlite"
        src_file.write_bytes(payload)
        src_file.chmod(0o644)
        dest_file = self.dest_dir / "app.sqlite"

        SubstituteTemplate(self.logger, self.replacement_map).copy(src_file, dest_file)

        self.assertEqual(dest_file.read_bytes(), payload)
        debug_messages = [call.args[0] for call in self.logger.debug.call_args_list]
        self.assertIn("Detected binary template app.sqlite; copying raw", debug_messages)

    def test_copy__falls_back_to_raw_for_non_utf8_binary_without_null_bytes(self):
        """Test that non-UTF-8 files without NUL bytes fall back cleanly to raw copy."""
        payload = b"BINARY $REPO_NAME \x80\x81\xfe\xff"
        src_file = self.src_dir / "blob.bin"
        src_file.write_bytes(payload)
        dest_file = self.dest_dir / "blob.bin"

        SubstituteTemplate(self.logger, self.replacement_map).copy(src_file, dest_file)

        self.assertEqual(dest_file.read_bytes(), payload)
        debug_messages = [call.args[0] for call in self.logger.debug.call_args_list]
        self.assertIn("Detected non-UTF-8 template blob.bin; copying raw", debug_messages)

    def test_copy__delegates_symlinks_to_raw_template(self):
        """Test that symlinks delegate directly to the raw strategy and remain symlinks."""
        (self.src_dir / "env.txt").write_text("REPO=$REPO_NAME\n")
        src_link = self.src_dir / "env_link.txt"
        src_link.symlink_to("env.txt")
        dest_link = self.dest_dir / "env_link.txt"

        raw_template = RawTemplate(self.logger)
        with patch.object(raw_template, "copy", wraps=raw_template.copy) as mock_raw_copy:
            SubstituteTemplate(self.logger, self.replacement_map, raw=raw_template).copy(
                src_link, dest_link
            )

        mock_raw_copy.assert_called_once_with(src_link, dest_link)
        self.assertTrue(dest_link.is_symlink())
        self.assertEqual(dest_link.readlink(), Path("env.txt"))

    def test_copy__unlinks_destination_symlink_without_clobbering_target(self):
        """Test that copy unlinks an existing destination symlink before writing output."""
        target = self.dest_dir / "target.txt"
        target.write_text("ORIGINAL\n")
        dest_symlink = self.dest_dir / "override.txt"
        dest_symlink.symlink_to(target)

        src_file = self.src_dir / "override.txt"
        src_file.write_text("SUB: $REPO_NAME\n")

        SubstituteTemplate(self.logger, self.replacement_map).copy(src_file, dest_symlink)

        self.assertFalse(dest_symlink.is_symlink())
        self.assertEqual(dest_symlink.read_text(), "SUB: test-repo\n")
        self.assertEqual(target.read_text(), "ORIGINAL\n")

    def test_copy__raises_when_destination_is_existing_directory(self):
        """Test that copy fails fast with IsADirectoryError when dest is a real directory."""
        existing_dir = self.dest_dir / "collision.txt"
        existing_dir.mkdir()
        src_file = self.src_dir / "collision.txt"
        src_file.write_text("SUB: $REPO_NAME\n")

        with self.assertRaises(IsADirectoryError):
            SubstituteTemplate(self.logger, self.replacement_map).copy(src_file, existing_dir)
        self.assertEqual(list(existing_dir.iterdir()), [])


class CowTemplateTestCase(unittest.TestCase):
    """Test case for the CowTemplate strategy."""

    def setUp(self):
        """Set up temporary source and destination directories."""
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.src_dir = self.tmp_path / "src"
        self.dest_dir = self.tmp_path / "dest"
        self.src_dir.mkdir()
        self.dest_dir.mkdir()
        self.logger = Mock()

    def tearDown(self):
        """Clean up temporary directories."""
        self.tmp_dir.cleanup()

    def test_copies_directories__is_true(self):
        """Test that CowTemplate opts into whole-directory copying."""
        self.assertTrue(CowTemplate(self.logger).copies_directories)

    def test_matches__matches_configured_patterns(self):
        """Test that CowTemplate inherits glob pattern matching from BaseTemplate."""
        template = CowTemplate(self.logger, patterns=[".cache", "data/*.db"])

        self.assertTrue(template.matches(Path(".cache")))
        self.assertTrue(template.matches(Path("data/app.db")))
        self.assertFalse(template.matches(Path("data/app.DB")))

    def test_copy__uses_linux_cp_flags_with_double_dash_separator(self):
        """Test that copy invokes Linux GNU cp with reflink flags and -- separator."""
        template = CowTemplate(self.logger)
        src_path = self.src_dir / "-weird.db"
        src_path.write_bytes(b"data")
        dest_path = self.dest_dir / "nested" / "-weird.db"

        with (
            patch("treefort.templates.sys.platform", "linux"),
            patch("treefort.templates.shutil.which", return_value="/usr/bin/cp"),
            patch("treefort.templates.subprocess.run") as mock_run,
        ):
            template.copy(src_path, dest_path)

        self.assertTrue(dest_path.parent.is_dir())
        mock_run.assert_called_once_with(
            [
                "cp",
                "-R",
                "--reflink=auto",
                "--preserve=mode,timestamps",
                "--",
                str(src_path),
                str(dest_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_copy__uses_darwin_cp_flags_with_double_dash_separator(self):
        """Test that copy invokes macOS cp -Rc -p with -- separator."""
        template = CowTemplate(self.logger)
        src_path = self.src_dir / "app.db"
        src_path.write_bytes(b"data")
        dest_path = self.dest_dir / "nested" / "app.db"

        with (
            patch("treefort.templates.sys.platform", "darwin"),
            patch("treefort.templates.shutil.which", return_value="/bin/cp"),
            patch("treefort.templates.subprocess.run") as mock_run,
        ):
            template.copy(src_path, dest_path)

        self.assertTrue(dest_path.parent.is_dir())
        mock_run.assert_called_once_with(
            [
                "cp",
                "-Rc",
                "-p",
                "--",
                str(src_path),
                str(dest_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_copy__falls_back_to_shutil_on_cp_called_process_error(self):
        """Test that copy logs DEBUG and falls back to shutil when cp exits non-zero."""
        template = CowTemplate(self.logger)

        src_file = self.src_dir / "app.db"
        src_file.write_bytes(b"FILE $REPO_NAME")
        src_file.chmod(0o755)

        src_cache = self.src_dir / ".cache"
        (src_cache / "sub").mkdir(parents=True)
        (src_cache / "sub" / "entry.txt").write_text("CACHE $REPO_NAME\n")
        (src_cache / "link.txt").symlink_to("sub/entry.txt")

        error = subprocess.CalledProcessError(1, ["cp"], stderr="reflink unsupported\n")
        with (
            patch("treefort.templates.shutil.which", return_value="/usr/bin/cp"),
            patch("treefort.templates.subprocess.run", side_effect=error),
        ):
            template.copy(src_file, self.dest_dir / "app.db")
            template.copy(src_cache, self.dest_dir / ".cache")

        copied_file = self.dest_dir / "app.db"
        self.assertEqual(copied_file.read_bytes(), b"FILE $REPO_NAME")
        self.assertEqual(copied_file.stat().st_mode & 0o777, 0o755)

        copied_dir = self.dest_dir / ".cache"
        self.assertEqual((copied_dir / "sub" / "entry.txt").read_text(), "CACHE $REPO_NAME\n")
        self.assertTrue((copied_dir / "link.txt").is_symlink())
        self.assertEqual((copied_dir / "link.txt").readlink(), Path("sub/entry.txt"))

        debug_messages = [call.args[0] for call in self.logger.debug.call_args_list]
        self.assertEqual(len(debug_messages), 2)
        for msg in debug_messages:
            self.assertIn("reflink unsupported", msg)

    def test_copy__falls_back_to_shutil_on_cp_os_error(self):
        """Test that copy logs DEBUG and falls back to shutil when cp raises OSError."""
        template = CowTemplate(self.logger)

        src_file = self.src_dir / "app.db"
        src_file.write_bytes(b"FILE DATA")

        src_cache = self.src_dir / ".cache"
        src_cache.mkdir()
        (src_cache / "item.txt").write_text("DIR DATA\n")

        with (
            patch("treefort.templates.shutil.which", return_value="/usr/bin/cp"),
            patch("treefort.templates.subprocess.run", side_effect=OSError("exec failed")),
        ):
            template.copy(src_file, self.dest_dir / "app.db")
            template.copy(src_cache, self.dest_dir / ".cache")

        self.assertEqual((self.dest_dir / "app.db").read_bytes(), b"FILE DATA")
        self.assertEqual((self.dest_dir / ".cache" / "item.txt").read_text(), "DIR DATA\n")

        debug_messages = [call.args[0] for call in self.logger.debug.call_args_list]
        self.assertEqual(len(debug_messages), 2)
        for msg in debug_messages:
            self.assertIn("exec failed", msg)

    def test_copy__falls_back_to_shutil_when_cp_is_missing(self):
        """Test that copy logs DEBUG and falls back to shutil when cp is not in PATH."""
        template = CowTemplate(self.logger)

        src_file = self.src_dir / "app.db"
        src_file.write_bytes(b"NO CP FILE")

        src_cache = self.src_dir / ".cache"
        src_cache.mkdir()
        (src_cache / "item.txt").write_text("NO CP DIR\n")
        (src_cache / "item_link.txt").symlink_to("item.txt")

        with (
            patch("treefort.templates.shutil.which", return_value=None),
            patch("treefort.templates.subprocess.run") as mock_run,
        ):
            template.copy(src_file, self.dest_dir / "app.db")
            template.copy(src_cache, self.dest_dir / ".cache")

        mock_run.assert_not_called()
        self.assertEqual((self.dest_dir / "app.db").read_bytes(), b"NO CP FILE")
        self.assertEqual((self.dest_dir / ".cache" / "item.txt").read_text(), "NO CP DIR\n")
        self.assertTrue((self.dest_dir / ".cache" / "item_link.txt").is_symlink())
        self.assertEqual((self.dest_dir / ".cache" / "item_link.txt").readlink(), Path("item.txt"))

        debug_messages = [call.args[0] for call in self.logger.debug.call_args_list]
        self.assertEqual(len(debug_messages), 2)

    def test_copy__recovers_from_partial_cp_dir_with_symlink_and_readonly_file(self):
        """Test that copy cleans partial cp output containing symlinks and 0o444 files."""
        template = CowTemplate(self.logger)

        src_modules = self.src_dir / "node_modules"
        (src_modules / "pkg").mkdir(parents=True)
        (src_modules / ".bin").mkdir(parents=True)
        src_readonly = src_modules / "pkg" / "readonly.txt"
        src_readonly.write_text("READONLY: $REPO_NAME\n")
        src_readonly.chmod(0o444)
        (src_modules / ".bin" / "tool").symlink_to("../pkg/readonly.txt")
        (src_modules / "extra.txt").write_text("EXTRA\n")

        dest_modules = self.dest_dir / "node_modules"

        def fail_after_partial_copy(*_args, **_kwargs):
            (dest_modules / "pkg").mkdir(parents=True, exist_ok=True)
            (dest_modules / ".bin").mkdir(parents=True, exist_ok=True)
            partial_readonly = dest_modules / "pkg" / "readonly.txt"
            partial_readonly.write_text("PARTIAL\n")
            partial_readonly.chmod(0o444)
            (dest_modules / ".bin" / "tool").symlink_to("../pkg/readonly.txt")
            raise subprocess.CalledProcessError(1, ["cp"], stderr="clone failed\n")

        with (
            patch("treefort.templates.shutil.which", return_value="/bin/cp"),
            patch("treefort.templates.subprocess.run", side_effect=fail_after_partial_copy),
        ):
            template.copy(src_modules, dest_modules)

        self.assertEqual(
            (dest_modules / "pkg" / "readonly.txt").read_text(), "READONLY: $REPO_NAME\n"
        )
        self.assertEqual((dest_modules / "pkg" / "readonly.txt").stat().st_mode & 0o777, 0o444)
        self.assertTrue((dest_modules / ".bin" / "tool").is_symlink())
        self.assertEqual((dest_modules / ".bin" / "tool").readlink(), Path("../pkg/readonly.txt"))
        self.assertEqual((dest_modules / "extra.txt").read_text(), "EXTRA\n")

        debug_messages = [call.args[0] for call in self.logger.debug.call_args_list]
        self.assertTrue(any("clone failed" in msg for msg in debug_messages))

    def test_copy__merges_into_existing_directory_with_symlinks_without_clobbering(self):
        """Test that merging an existing dir replaces symlinks safely without clobbering targets."""
        dest_cache = self.dest_dir / ".cache"
        (dest_cache / "sub").mkdir(parents=True)
        (dest_cache / "target.txt").write_text("ORIGINAL TARGET\n")
        (dest_cache / "global_only.txt").write_text("GLOBAL\n")
        (dest_cache / "shared_link").symlink_to("target.txt")
        (dest_cache / "file_over_link").symlink_to("target.txt")
        (dest_cache / "link_over_file").write_text("OLD REGULAR\n")
        (dest_cache / "sub" / "from_global.txt").write_text("SUB GLOBAL\n")

        src_cache = self.src_dir / ".cache"
        (src_cache / "sub").mkdir(parents=True)
        (src_cache / "new_target.txt").write_text("NEW TARGET\n")
        (src_cache / "shared_link").symlink_to("new_target.txt")
        (src_cache / "file_over_link").write_text("PROJECT FILE\n")
        (src_cache / "link_over_file").symlink_to("new_target.txt")
        (src_cache / "sub" / "from_project.txt").write_text("SUB PROJECT\n")

        with patch("treefort.templates.subprocess.run") as mock_run:
            CowTemplate(self.logger).copy(src_cache, dest_cache)

        mock_run.assert_not_called()
        self.assertFalse((dest_cache / ".cache").exists())
        self.assertEqual((dest_cache / "global_only.txt").read_text(), "GLOBAL\n")
        self.assertEqual((dest_cache / "sub" / "from_global.txt").read_text(), "SUB GLOBAL\n")
        self.assertEqual((dest_cache / "sub" / "from_project.txt").read_text(), "SUB PROJECT\n")
        self.assertTrue((dest_cache / "shared_link").is_symlink())
        self.assertEqual((dest_cache / "shared_link").readlink(), Path("new_target.txt"))
        self.assertFalse((dest_cache / "file_over_link").is_symlink())
        self.assertEqual((dest_cache / "file_over_link").read_text(), "PROJECT FILE\n")
        self.assertEqual((dest_cache / "target.txt").read_text(), "ORIGINAL TARGET\n")
        self.assertTrue((dest_cache / "link_over_file").is_symlink())
        self.assertEqual((dest_cache / "link_over_file").readlink(), Path("new_target.txt"))

    def test_copy__raises_when_directory_targets_existing_symlink(self):
        """Test that copy fails fast when a directory targets an existing or broken symlink."""
        template = CowTemplate(self.logger)

        src_cache = self.src_dir / ".cache"
        src_cache.mkdir()
        (src_cache / "item.txt").write_text("NEW\n")

        victim_dir = self.dest_dir / "victim_dir"
        victim_dir.mkdir()
        (victim_dir / "keep.txt").write_text("KEEP\n")
        dest_dir_link = self.dest_dir / "dir_link"
        dest_dir_link.symlink_to(victim_dir, target_is_directory=True)

        dest_broken_link = self.dest_dir / "broken_link"
        dest_broken_link.symlink_to("nonexistent_target")

        with self.assertRaises(FileExistsError):
            template.copy(src_cache, dest_dir_link)
        self.assertFalse((victim_dir / "item.txt").exists())

        with self.assertRaises(FileExistsError):
            template.copy(src_cache, dest_broken_link)

    def test_copy__raises_when_non_dir_destination_is_existing_directory(self):
        """Test that copy fails fast with IsADirectoryError for non-dir over real dir."""
        template = CowTemplate(self.logger)

        existing_file_dir = self.dest_dir / "collision.db"
        existing_file_dir.mkdir()
        existing_file_link_dir = self.dest_dir / "collision_link"
        existing_file_link_dir.mkdir()
        existing_dir_link_dir = self.dest_dir / "collision_dir_link"
        existing_dir_link_dir.mkdir()

        (self.src_dir / "env.txt").write_text("ENV\n")
        (self.src_dir / "subdir").mkdir()
        cow_file = self.src_dir / "collision.db"
        cow_file.write_bytes(b"COW DATA")
        file_link = self.src_dir / "collision_link"
        file_link.symlink_to("env.txt")
        dir_link = self.src_dir / "collision_dir_link"
        dir_link.symlink_to("subdir", target_is_directory=True)

        with self.assertRaises(IsADirectoryError):
            template.copy(cow_file, existing_file_dir)
        self.assertEqual(list(existing_file_dir.iterdir()), [])

        with self.assertRaises(IsADirectoryError):
            template.copy(file_link, existing_file_link_dir)
        self.assertEqual(list(existing_file_link_dir.iterdir()), [])

        with self.assertRaises(IsADirectoryError):
            template.copy(dir_link, existing_dir_link_dir)
        self.assertEqual(list(existing_dir_link_dir.iterdir()), [])

    def test_copy__unlinks_existing_destination_symlink_and_overwrites_file_with_symlink(self):
        """Test that copy safely replaces symlinks and overwrites files with symlinks."""
        template = CowTemplate(self.logger)

        victim = self.dest_dir / "victim.txt"
        victim.write_text("ORIGINAL\n")
        dest_symlink = self.dest_dir / "override.db"
        dest_symlink.symlink_to(victim)

        src_file = self.src_dir / "override.db"
        src_file.write_text("NEW COW\n")
        template.copy(src_file, dest_symlink)

        self.assertFalse(dest_symlink.is_symlink())
        self.assertEqual(dest_symlink.read_text(), "NEW COW\n")
        self.assertEqual(victim.read_text(), "ORIGINAL\n")

        dest_regular = self.dest_dir / "replace_with_link"
        dest_regular.write_text("OLD REGULAR\n")
        src_link = self.src_dir / "replace_with_link"
        src_link.symlink_to("env.txt")
        template.copy(src_link, dest_regular)

        self.assertTrue(dest_regular.is_symlink())
        self.assertEqual(dest_regular.readlink(), Path("env.txt"))


class TemplatesTestCase(unittest.TestCase):
    """Test case for the Templates class."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        self.global_dir = self.tmp_path / "global"
        self.project_dir = self.tmp_path / "project"
        self.global_templates_dir = self.global_dir / "templates"
        self.project_templates_dir = self.project_dir / ".gh" / "worktree" / "templates"

        self.global_templates_dir.mkdir(parents=True)
        self.project_templates_dir.mkdir(parents=True)

        # Create default template files
        (self.global_templates_dir / "config.txt").write_text(
            "REPO: $REPO_NAME\nWORKTREE: $WORKTREE_NAME\n"
        )

        (self.project_templates_dir / "env.txt").write_text("PATH: $WORKTREE_DIR\nUSER: $USER\n")

        # Create a subdirectory with a template
        subdir = self.project_templates_dir / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("NESTED: $REPO_DIR\n")

        self.config = SimpleNamespace(name="test-repo")
        self.global_config = SimpleNamespace(allowed_envvars=[])

    def tearDown(self):
        """Clean up test fixtures after each test method."""
        self.tmp_dir.cleanup()

    def _create_context(self, allowed_envvars=None):
        """Helper method to create a context with custom allowed_envvars."""
        if allowed_envvars is not None:
            self.global_config = SimpleNamespace(allowed_envvars=allowed_envvars)

        return StubContext(
            self.project_dir,
            self.config,
            self.global_config,
            self.project_dir / ".gh" / "worktree",
            self.global_dir,
        )

    @patch.dict(os.environ, {"USER": "testuser", "HOME": "/home/testuser"})
    def test_init__initializes_replacement_map_with_env_vars(self):
        """Test that initialization populates replacement_map with environment variables."""
        context = self._create_context(allowed_envvars=["USER", "HOME"])

        templates = Templates(context, Mock())

        self.assertEqual(templates.replacement_map["USER"], "testuser")
        self.assertIn("HOME", templates.replacement_map)
        self.assertEqual(templates.replacement_map["HOME"], "/home/testuser")

    def test_init__handles_missing_env_vars(self):
        """Test that initialization handles missing environment variables gracefully."""
        context = self._create_context(allowed_envvars=["NONEXISTENT_VAR"])

        templates = Templates(context, Mock())

        self.assertIn("NONEXISTENT_VAR", templates.replacement_map)
        self.assertEqual(templates.replacement_map["NONEXISTENT_VAR"], "")

    def test_build_handlers__prefers_cow_over_raw_over_substitute(self):
        """Test that handler resolution prioritizes CowTemplate > RawTemplate > Substitute."""
        global_config = SimpleNamespace(
            allowed_envvars=[],
            template_cow_patterns=["*.db"],
            template_raw_patterns=["*.db", "data/*"],
        )
        config = SimpleNamespace(name="test-repo", template_raw_patterns=["*.png"])
        templates = Templates(self._create_context(), Mock())

        handlers = templates._build_handlers(global_config, config)
        walk_ctx = _TemplateWalkContext(
            templates_dir=self.project_templates_dir,
            templates_root=self.project_templates_dir.resolve(),
            worktree_dir=self.project_dir / "my-worktree",
            handlers=handlers,
        )

        self.assertIsInstance(walk_ctx.resolve_handler(Path("data/app.db")), CowTemplate)
        self.assertIsInstance(walk_ctx.resolve_handler(Path("data/info.txt")), RawTemplate)
        self.assertIsInstance(walk_ctx.resolve_handler(Path("assets/logo.png")), RawTemplate)
        self.assertIsInstance(walk_ctx.resolve_handler(Path("config.txt")), SubstituteTemplate)

    @patch.dict(os.environ, {"USER": "testuser"})
    def test_copy__creates_worktree_files(self):
        """Test that copy creates files in the worktree directory."""
        context = self._create_context(allowed_envvars=["USER"])
        templates = Templates(context, Mock())

        # Create worktree directory (normally done by git worktree add)
        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        self.assertTrue((worktree_dir / "config.txt").exists())
        self.assertTrue((worktree_dir / "env.txt").exists())
        self.assertTrue((worktree_dir / "subdir" / "nested.txt").exists())

    @patch.dict(os.environ, {"USER": "testuser"})
    def test_copy__substitutes_variables_correctly(self):
        """Test that copy performs variable substitution correctly."""
        context = self._create_context(allowed_envvars=["USER"])
        templates = Templates(context, Mock())

        # Create worktree directory (normally done by git worktree add)
        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        config_content = (worktree_dir / "config.txt").read_text()
        self.assertIn("REPO: test-repo", config_content)
        self.assertIn("WORKTREE: my-worktree", config_content)

        env_content = (worktree_dir / "env.txt").read_text()
        self.assertIn(f"PATH: {worktree_dir}", env_content)
        self.assertIn("USER: testuser", env_content)

        nested_content = (worktree_dir / "subdir" / "nested.txt").read_text()
        self.assertIn(f"NESTED: {self.project_dir}", nested_content)

    @patch.dict(os.environ, {"USER": "testuser"})
    def test_copy__substitutes_normalized_worktree_name(self):
        """Test that copy provides WORKTREE_NAME_NORMALIZED variable."""
        context = self._create_context(allowed_envvars=["USER"])
        templates = Templates(context, Mock())

        # Create worktree directory (normally done by git worktree add)
        worktree_dir = self.project_dir / "feat/some-feature"
        worktree_dir.mkdir(parents=True)

        # Add a template that uses the normalized name
        (self.project_templates_dir / "normalized.txt").write_text(
            "NORMALIZED: $WORKTREE_NAME_NORMALIZED\n"
        )

        templates.copy("feat/some-feature")

        normalized_content = (worktree_dir / "normalized.txt").read_text()
        self.assertIn("NORMALIZED: feat-some-feature", normalized_content)

    def test_copy__normalized_replaces_underscores_with_dashes(self):
        """Test that WORKTREE_NAME_NORMALIZED replaces underscores with dashes."""
        context = self._create_context()
        templates = Templates(context, Mock())

        worktree_dir = self.project_dir / "my_feature-branch"
        worktree_dir.mkdir(parents=True)

        (self.project_templates_dir / "normalized.txt").write_text(
            "NORMALIZED: $WORKTREE_NAME_NORMALIZED\n"
        )

        templates.copy("my_feature-branch")

        normalized_content = (worktree_dir / "normalized.txt").read_text()
        self.assertIn("NORMALIZED: my-feature-branch", normalized_content)

    def test_copy__substitutes_default_branch(self):
        """Test that copy provides DEFAULT_BRANCH and TREEFORT_DEFAULT_BRANCH."""
        self.config = SimpleNamespace(name="test-repo", default_branch="develop")
        context = self._create_context()
        templates = Templates(context, Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        (self.project_templates_dir / "branch.txt").write_text(
            "BRANCH: $DEFAULT_BRANCH\nPREFIXED: $TREEFORT_DEFAULT_BRANCH\n"
        )

        templates.copy("my-worktree")

        content = (worktree_dir / "branch.txt").read_text()
        self.assertIn("BRANCH: develop", content)
        self.assertIn("PREFIXED: develop", content)

    def test_copy__defaults_missing_default_branch_to_main(self):
        """Test that copy falls back to main when the config omits default_branch."""
        context = self._create_context()
        templates = Templates(context, Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        (self.project_templates_dir / "branch.txt").write_text("BRANCH: $DEFAULT_BRANCH\n")

        templates.copy("my-worktree")

        self.assertIn("BRANCH: main", (worktree_dir / "branch.txt").read_text())

    def test_copy__substitutes_treefort_prefixed_variables(self):
        """Test that copy mirrors every key with a TREEFORT_ prefix."""
        context = self._create_context()
        templates = Templates(context, Mock())

        worktree_dir = self.project_dir / "feat/some-feature"
        worktree_dir.mkdir(parents=True)

        (self.project_templates_dir / "prefixed.txt").write_text(
            "REPO_NAME: $TREEFORT_REPO_NAME\n"
            "REPO_DIR: $TREEFORT_REPO_DIR\n"
            "WORKTREE_NAME: $TREEFORT_WORKTREE_NAME\n"
            "WORKTREE_NAME_NORMALIZED: $TREEFORT_WORKTREE_NAME_NORMALIZED\n"
            "WORKTREE_DIR: $TREEFORT_WORKTREE_DIR\n"
        )

        templates.copy("feat/some-feature")

        content = (worktree_dir / "prefixed.txt").read_text()
        self.assertIn("REPO_NAME: test-repo", content)
        self.assertIn(f"REPO_DIR: {self.project_dir}", content)
        self.assertIn("WORKTREE_NAME: feat/some-feature", content)
        self.assertIn("WORKTREE_NAME_NORMALIZED: feat-some-feature", content)
        self.assertIn(f"WORKTREE_DIR: {worktree_dir}", content)

    def test_copy__preserves_file_permissions(self):
        """Test that copy preserves file permissions."""
        executable_file = self.project_templates_dir / "script.sh"
        executable_file.write_text("#!/bin/bash\necho $REPO_NAME\n")
        executable_file.chmod(0o755)

        context = self._create_context()
        templates = Templates(context, Mock())

        # Create worktree directory (normally done by git worktree add)
        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        worktree_script = worktree_dir / "script.sh"
        self.assertTrue(worktree_script.exists())
        self.assertTrue(worktree_script.stat().st_mode & 0o111)

    def test_copy__handles_no_templates_directory(self):
        """Test that copy works when no template directories exist."""
        empty_project = self.tmp_path / "empty_project"
        context = StubContext(
            empty_project,
            self.config,
            self.global_config,
            empty_project / ".gh" / "worktree",
            self.tmp_path / "empty_global",
        )
        templates = Templates(context, Mock())

        worktree_dir = empty_project / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        self.assertEqual(len(list(worktree_dir.iterdir())), 0)

    def test_copy__handles_undefined_variables_with_safe_substitute(self):
        """Test that copy uses safe_substitute to handle missing variables."""
        (self.project_templates_dir / "missing.txt").write_text(
            "DEFINED: $REPO_NAME\nUNDEFINED: $MISSING_VAR\n"
        )

        context = self._create_context()
        templates = Templates(context, Mock())

        # Create worktree directory (normally done by git worktree add)
        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        worktree_file = worktree_dir / "missing.txt"
        content = worktree_file.read_text()

        self.assertIn("DEFINED: test-repo", content)
        self.assertIn("UNDEFINED: $MISSING_VAR", content)

    def test_copy__creates_subdirectories(self):
        """Test that copy creates necessary subdirectories."""
        context = self._create_context()
        templates = Templates(context, Mock())

        # Create worktree directory (normally done by git worktree add)
        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        self.assertTrue((worktree_dir / "subdir").is_dir())

    def test_copy__logs_per_file_copies_at_debug(self):
        """Test that per-file copy messages are demoted to DEBUG."""
        context = self._create_context()
        templates = Templates(context, Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        debug_messages = [call.args[0] for call in templates.logger.debug.call_args_list]
        self.assertIn("Copying template config.txt", debug_messages)

        info_messages = [call.args[0] for call in templates.logger.info.call_args_list]
        self.assertFalse(any(message.startswith("Copying template") for message in info_messages))

    def test_copy__logs_summary_of_copied_templates(self):
        """Test that a single copy summary is logged at INFO."""
        context = self._create_context()
        templates = Templates(context, Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        with patch("treefort.ui.is_color_enabled", return_value=False):
            templates.copy("my-worktree")

        templates.logger.info.assert_called_once_with("✓ Copied 3 template(s)")

    def test_copy__logs_no_summary_without_templates(self):
        """Test that no summary is logged when there are no templates to copy."""
        context = self._create_context()
        templates = Templates(context, Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)
        shutil.rmtree(self.global_templates_dir)
        shutil.rmtree(self.project_templates_dir)

        templates.copy("my-worktree")

        templates.logger.info.assert_not_called()

    def test_is_safe_template_path__returns_true_for_regular_file(self):
        """Test that regular non-symlink files inside templates are considered safe."""
        templates = Templates(self._create_context(), Mock())
        regular_file = self.project_templates_dir / "env.txt"

        self.assertTrue(templates._is_safe_template_path(regular_file, self.project_templates_dir))

    def test_is_safe_template_path__returns_true_for_internal_symlink(self):
        """Test that a symlink resolving inside templates_root is considered safe."""
        templates = Templates(self._create_context(), Mock())
        internal_link = self.project_templates_dir / "env_link.txt"
        internal_link.symlink_to("env.txt")

        self.assertTrue(templates._is_safe_template_path(internal_link, self.project_templates_dir))

    def test_is_safe_template_path__returns_false_for_escaping_symlink(self):
        """Test that absolute, relative, and sibling-prefix escaping symlinks are rejected."""
        templates = Templates(self._create_context(), Mock())
        outside_file = self.tmp_path / "outside.txt"
        outside_file.write_text("secret\n")
        escaping_link = self.project_templates_dir / "escape.txt"
        escaping_link.symlink_to(outside_file)

        parent_outside = self.project_templates_dir.parent / "outside.txt"
        parent_outside.write_text("parent secret\n")
        rel_escape_link = self.project_templates_dir / "subdir" / "rel_escape.txt"
        rel_escape_link.symlink_to("../../outside.txt")

        sibling_dir = self.project_templates_dir.parent / "templates_evil"
        sibling_dir.mkdir()
        sibling_secret = sibling_dir / "secret.txt"
        sibling_secret.write_text("sibling secret\n")
        sibling_link = self.project_templates_dir / "sibling_escape.txt"
        sibling_link.symlink_to(sibling_secret)

        self.assertFalse(
            templates._is_safe_template_path(escaping_link, self.project_templates_dir)
        )
        self.assertFalse(
            templates._is_safe_template_path(rel_escape_link, self.project_templates_dir)
        )
        self.assertFalse(templates._is_safe_template_path(sibling_link, self.project_templates_dir))

    def test_is_safe_template_path__returns_false_for_circular_symlink(self):
        """Test that a circular symlink loop returns False rather than raising."""
        templates = Templates(self._create_context(), Mock())
        link_a = self.project_templates_dir / "loop_a"
        link_b = self.project_templates_dir / "loop_b"
        link_a.symlink_to("loop_b")
        link_b.symlink_to("loop_a")

        self.assertFalse(templates._is_safe_template_path(link_a, self.project_templates_dir))

    def test_copy__raw_patterns_preserve_literal_variables_and_permissions(self):
        """Test that raw patterns copy files verbatim without substitution and keep mode."""
        scripts_dir = self.project_templates_dir / "scripts"
        scripts_dir.mkdir()
        raw_script = scripts_dir / "deploy.sh"
        raw_script.write_text("#!/bin/sh\necho ${REPO_NAME} $WORKTREE_NAME\n")
        raw_script.chmod(0o755)

        self.config = SimpleNamespace(name="test-repo", template_raw_patterns=["scripts/*.sh"])
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        copied_script = worktree_dir / "scripts" / "deploy.sh"
        self.assertEqual(
            copied_script.read_text(),
            "#!/bin/sh\necho ${REPO_NAME} $WORKTREE_NAME\n",
        )
        self.assertEqual(copied_script.stat().st_mode & 0o777, 0o755)

    def test_copy__combines_global_and_project_raw_and_cow_patterns(self):
        """Test that all four global/project raw and cow pattern lists apply independently."""
        (self.global_templates_dir / "from_global.graw").write_text("GLOBAL_RAW: ${REPO_NAME}\n")
        (self.project_templates_dir / "from_project.praw").write_text(
            "PROJECT_RAW: $WORKTREE_NAME\n"
        )
        (self.global_templates_dir / "from_global.gcow").write_text("GLOBAL_COW: ${REPO_NAME}\n")
        (self.project_templates_dir / "from_project.pcow").write_text(
            "PROJECT_COW: $WORKTREE_NAME\n"
        )

        self.global_config = SimpleNamespace(
            allowed_envvars=[],
            template_raw_patterns=["*.graw"],
            template_cow_patterns=["*.gcow"],
        )
        self.config = SimpleNamespace(
            name="test-repo",
            template_raw_patterns=["*.praw"],
            template_cow_patterns=["*.pcow"],
        )
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        self.assertEqual(
            (worktree_dir / "from_global.graw").read_text(),
            "GLOBAL_RAW: ${REPO_NAME}\n",
        )
        self.assertEqual(
            (worktree_dir / "from_project.praw").read_text(),
            "PROJECT_RAW: $WORKTREE_NAME\n",
        )
        self.assertEqual(
            (worktree_dir / "from_global.gcow").read_text(),
            "GLOBAL_COW: ${REPO_NAME}\n",
        )
        self.assertEqual(
            (worktree_dir / "from_project.pcow").read_text(),
            "PROJECT_COW: $WORKTREE_NAME\n",
        )

    def test_copy__prefers_cow_over_raw_when_patterns_overlap(self):
        """Test that overlapping cow and raw patterns route through CowTemplate via copy()."""
        db_file = self.project_templates_dir / "app.db"
        db_file.write_text("DB: $REPO_NAME\n")
        cache_dir = self.project_templates_dir / ".cache"
        cache_dir.mkdir()
        (cache_dir / "state.txt").write_text("STATE: $WORKTREE_NAME\n")

        self.global_config = SimpleNamespace(
            allowed_envvars=[],
            template_raw_patterns=["*.db", ".cache"],
            template_cow_patterns=[],
        )
        self.config = SimpleNamespace(
            name="test-repo",
            template_raw_patterns=[],
            template_cow_patterns=["*.db", ".cache"],
        )
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        with (
            patch.object(
                CowTemplate, "copy", autospec=True, side_effect=CowTemplate.copy
            ) as mock_cow,
            patch.object(
                RawTemplate, "copy", autospec=True, side_effect=RawTemplate.copy
            ) as mock_raw,
        ):
            templates.copy("my-worktree")

        cow_sources = [call.args[1] for call in mock_cow.call_args_list]
        self.assertEqual(cow_sources, [cache_dir, db_file])
        mock_raw.assert_not_called()
        self.assertEqual((worktree_dir / "app.db").read_text(), "DB: $REPO_NAME\n")
        self.assertEqual(
            (worktree_dir / ".cache" / "state.txt").read_text(),
            "STATE: $WORKTREE_NAME\n",
        )

    def test_copy__raises_when_regular_directory_targets_existing_symlink(self):
        """Test that non-COW directory traversal refuses to write through a destination symlink."""
        victim_dir = self.tmp_path / "victim_dir"
        victim_dir.mkdir()

        templates = Templates(self._create_context(), Mock())
        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)
        (worktree_dir / "subdir").symlink_to(victim_dir, target_is_directory=True)

        with self.assertRaises(FileExistsError):
            templates.copy("my-worktree")

        self.assertFalse((victim_dir / "nested.txt").exists())

    def test_copy__falls_back_to_raw_for_binary_file_with_null_bytes(self):
        """Test that valid-UTF-8 payloads containing NUL bytes trigger binary raw fallback."""
        payload = b"SQLite format 3\x00$REPO_NAME\n"
        binary_file = self.project_templates_dir / "app.sqlite"
        binary_file.write_bytes(payload)
        binary_file.chmod(0o644)

        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        copied_file = worktree_dir / "app.sqlite"
        self.assertEqual(copied_file.read_bytes(), payload)
        debug_messages = [call.args[0] for call in templates.logger.debug.call_args_list]
        self.assertIn("Detected binary template app.sqlite; copying raw", debug_messages)

    def test_copy__falls_back_to_raw_for_non_utf8_binary_without_null_bytes(self):
        """Test that non-UTF-8 files without NUL bytes fall back cleanly to raw copy."""
        payload = b"BINARY $REPO_NAME \x80\x81\xfe\xff"
        (self.project_templates_dir / "blob.bin").write_bytes(payload)

        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        self.assertEqual((worktree_dir / "blob.bin").read_bytes(), payload)
        debug_messages = [call.args[0] for call in templates.logger.debug.call_args_list]
        self.assertIn("Detected non-UTF-8 template blob.bin; copying raw", debug_messages)

    def test_copy__skips_and_warns_for_escaping_file_and_dir_symlinks(self):
        """Test that symlinks resolving outside templates_dir warn and are skipped."""
        outside_file = self.tmp_path / "secret.txt"
        outside_file.write_text("TOP SECRET\n")
        outside_dir = self.tmp_path / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / "leak.txt").write_text("LEAKED\n")

        (self.project_templates_dir / "evil_file.txt").symlink_to(outside_file)
        (self.project_templates_dir / "evil_dir").symlink_to(outside_dir, target_is_directory=True)

        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        with patch("treefort.ui.is_color_enabled", return_value=False):
            templates.copy("my-worktree")

        self.assertFalse((worktree_dir / "evil_file.txt").exists())
        self.assertFalse((worktree_dir / "evil_file.txt").is_symlink())
        self.assertFalse((worktree_dir / "evil_dir").exists())
        self.assertFalse((worktree_dir / "evil_dir").is_symlink())
        self.assertEqual(templates.logger.warning.call_count, 2)
        templates.logger.info.assert_called_once_with("✓ Copied 3 template(s)")

    def test_copy__preserves_internal_file_and_dir_symlinks_without_recursing(self):
        """Test that internal file and dir symlinks are copied as symlinks and counted once."""
        (self.project_templates_dir / "env_link.txt").symlink_to("env.txt")
        (self.project_templates_dir / "subdir_link").symlink_to("subdir", target_is_directory=True)

        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        with patch("treefort.ui.is_color_enabled", return_value=False):
            templates.copy("my-worktree")

        file_link = worktree_dir / "env_link.txt"
        dir_link = worktree_dir / "subdir_link"
        self.assertTrue(file_link.is_symlink())
        self.assertEqual(file_link.readlink(), Path("env.txt"))
        self.assertTrue(dir_link.is_symlink())
        self.assertEqual(dir_link.readlink(), Path("subdir"))

        debug_messages = [call.args[0] for call in templates.logger.debug.call_args_list]
        self.assertIn("Copying template env_link.txt", debug_messages)
        self.assertIn("Copying template subdir_link", debug_messages)
        self.assertNotIn("Copying template subdir_link/nested.txt", debug_messages)
        templates.logger.info.assert_called_once_with("✓ Copied 5 template(s)")

    def test_copy__project_symlink_overrides_global_symlink_and_regular_file(self):
        """Test that a project symlink cleanly overrides a global symlink or regular file."""
        (self.global_templates_dir / "global_target.txt").write_text("GLOBAL\n")
        (self.global_templates_dir / "shared_link").symlink_to("global_target.txt")
        (self.global_templates_dir / "was_regular.txt").write_text("OLD\n")

        (self.project_templates_dir / "project_target.txt").write_text("PROJECT\n")
        (self.project_templates_dir / "shared_link").symlink_to("project_target.txt")
        (self.project_templates_dir / "was_regular.txt").symlink_to("project_target.txt")

        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        self.assertTrue((worktree_dir / "shared_link").is_symlink())
        self.assertEqual((worktree_dir / "shared_link").readlink(), Path("project_target.txt"))
        self.assertTrue((worktree_dir / "was_regular.txt").is_symlink())
        self.assertEqual((worktree_dir / "was_regular.txt").readlink(), Path("project_target.txt"))

    def test_copy__project_file_overrides_global_symlink_without_clobbering_target(self):
        """Test that project files unlink global symlinks instead of writing through them."""
        (self.global_templates_dir / "target.txt").write_text("ORIGINAL\n")
        (self.global_templates_dir / "override_sub.txt").symlink_to("target.txt")
        (self.global_templates_dir / "override_raw.bin").symlink_to("target.txt")

        (self.project_templates_dir / "override_sub.txt").write_text("SUB: $REPO_NAME\n")
        (self.project_templates_dir / "override_raw.bin").write_text("RAW: $REPO_NAME\n")

        self.config = SimpleNamespace(name="test-repo", template_raw_patterns=["*.bin"])
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        sub_file = worktree_dir / "override_sub.txt"
        raw_file = worktree_dir / "override_raw.bin"
        self.assertFalse(sub_file.is_symlink())
        self.assertEqual(sub_file.read_text(), "SUB: test-repo\n")
        self.assertFalse(raw_file.is_symlink())
        self.assertEqual(raw_file.read_text(), "RAW: $REPO_NAME\n")
        self.assertEqual((worktree_dir / "target.txt").read_text(), "ORIGINAL\n")

    def test_copy__excludes_regular_subdirectories_from_count_and_debug_logs(self):
        """Test that regular subdirectories are created without logging or incrementing count."""
        (self.project_templates_dir / "empty_dir").mkdir()
        nested_dir = self.project_templates_dir / "a" / "b"
        nested_dir.mkdir(parents=True)
        (nested_dir / "deep.txt").write_text("DEEP: $REPO_NAME\n")

        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        with patch("treefort.ui.is_color_enabled", return_value=False):
            templates.copy("my-worktree")

        self.assertTrue((worktree_dir / "empty_dir").is_dir())
        self.assertEqual((worktree_dir / "a" / "b" / "deep.txt").read_text(), "DEEP: test-repo\n")
        templates.logger.info.assert_called_once_with("✓ Copied 4 template(s)")

        debug_messages = [call.args[0] for call in templates.logger.debug.call_args_list]
        self.assertIn("Copying template a/b/deep.txt", debug_messages)
        for directory_rel in ("subdir", "empty_dir", "a", "a/b"):
            self.assertNotIn(f"Copying template {directory_rel}", debug_messages)

    def test_copy__cow_directory_prunes_traversal_and_counts_as_one(self):
        """Test that a COW directory copies once without Python walking nested entries."""
        cache_dir = self.project_templates_dir / ".cache"
        nested_dir = cache_dir / "sub"
        nested_dir.mkdir(parents=True)
        (cache_dir / "index.bin").write_bytes(b"IDX\x00$REPO_NAME")
        (nested_dir / "nested.txt").write_text("LITERAL: $REPO_NAME\n")

        self.config = SimpleNamespace(name="test-repo", template_cow_patterns=[".cache"])
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        original_cow_copy = CowTemplate.copy
        with (
            patch("treefort.ui.is_color_enabled", return_value=False),
            patch.object(
                templates, "_walk_templates", wraps=templates._walk_templates
            ) as mock_walk,
            patch.object(
                CowTemplate, "copy", autospec=True, side_effect=original_cow_copy
            ) as mock_cow_copy,
        ):
            templates.copy("my-worktree")

        mock_cow_copy.assert_called_once()
        self.assertEqual(mock_cow_copy.call_args.args[1:], (cache_dir, worktree_dir / ".cache"))
        walked_dirs = [call.args[0] for call in mock_walk.call_args_list]
        self.assertNotIn(cache_dir, walked_dirs)
        self.assertNotIn(nested_dir, walked_dirs)

        self.assertEqual((worktree_dir / ".cache" / "index.bin").read_bytes(), b"IDX\x00$REPO_NAME")
        self.assertEqual(
            (worktree_dir / ".cache" / "sub" / "nested.txt").read_text(),
            "LITERAL: $REPO_NAME\n",
        )

        debug_messages = [call.args[0] for call in templates.logger.debug.call_args_list]
        self.assertIn("Copying template .cache", debug_messages)
        self.assertFalse(
            any(msg.startswith("Copying template .cache/") for msg in debug_messages),
            f"Expected pruned COW subtree logs, got: {debug_messages}",
        )
        templates.logger.info.assert_called_once_with("✓ Copied 4 template(s)")

    def test_copy__cow_file_dispatches_to_cow_template_instead_of_raw_template(self):
        """Test that files matching cow patterns dispatch to CowTemplate.copy verbatim."""
        data_dir = self.project_templates_dir / "data"
        data_dir.mkdir()
        src_db = data_dir / "app.db"
        src_db.write_text("DB: $REPO_NAME\n")

        self.config = SimpleNamespace(name="test-repo", template_cow_patterns=["*.db"])
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        original_cow_copy = CowTemplate.copy
        original_raw_copy = RawTemplate.copy
        with (
            patch.object(
                CowTemplate, "copy", autospec=True, side_effect=original_cow_copy
            ) as mock_cow_copy,
            patch.object(
                RawTemplate, "copy", autospec=True, side_effect=original_raw_copy
            ) as mock_raw_copy,
        ):
            templates.copy("my-worktree")

        mock_cow_copy.assert_called_once()
        self.assertEqual(
            mock_cow_copy.call_args.args[1:],
            (src_db, worktree_dir / "data" / "app.db"),
        )
        mock_raw_copy.assert_not_called()
        self.assertEqual((worktree_dir / "data" / "app.db").read_text(), "DB: $REPO_NAME\n")

    def test_copy__merges_global_and_project_cow_directories_without_nesting(self):
        """Test that global and project COW directories merge cleanly without nesting."""
        global_cache = self.global_templates_dir / ".cache"
        (global_cache / "sub").mkdir(parents=True)
        (global_cache / "global_only.txt").write_text("GLOBAL: $REPO_NAME\n")
        (global_cache / "shared.txt").write_text("FROM GLOBAL\n")
        (global_cache / "sub" / "from_global.txt").write_text("SUB GLOBAL\n")

        project_cache = self.project_templates_dir / ".cache"
        (project_cache / "sub").mkdir(parents=True)
        (project_cache / "project_only.txt").write_text("PROJECT: $WORKTREE_NAME\n")
        (project_cache / "shared.txt").write_text("FROM PROJECT\n")
        (project_cache / "sub" / "from_project.txt").write_text("SUB PROJECT\n")

        self.config = SimpleNamespace(name="test-repo", template_cow_patterns=[".cache"])
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        with patch("treefort.templates.subprocess.run", wraps=subprocess.run) as mock_run:
            templates.copy("my-worktree")

        # Global copy uses cp once; project merge sees existing dest dir and merges directly.
        self.assertEqual(mock_run.call_count, 1)
        self.assertEqual(mock_run.call_args.args[0][-2], str(global_cache))

        dest_cache = worktree_dir / ".cache"
        self.assertFalse((dest_cache / ".cache").exists())
        self.assertEqual((dest_cache / "global_only.txt").read_text(), "GLOBAL: $REPO_NAME\n")
        self.assertEqual((dest_cache / "project_only.txt").read_text(), "PROJECT: $WORKTREE_NAME\n")
        self.assertEqual((dest_cache / "shared.txt").read_text(), "FROM PROJECT\n")
        self.assertEqual((dest_cache / "sub" / "from_global.txt").read_text(), "SUB GLOBAL\n")
        self.assertEqual((dest_cache / "sub" / "from_project.txt").read_text(), "SUB PROJECT\n")

    def test_copy__merges_cow_directories_with_overlapping_symlinks_and_file_over_symlink(self):
        """Test that merging COW dirs replaces symlinks safely without clobbering targets."""
        global_cache = self.global_templates_dir / ".cache"
        global_cache.mkdir()
        (global_cache / "target.txt").write_text("ORIGINAL TARGET\n")
        (global_cache / "shared_link").symlink_to("target.txt")
        (global_cache / "file_over_link").symlink_to("target.txt")
        (global_cache / "link_over_file").write_text("OLD REGULAR\n")

        project_cache = self.project_templates_dir / ".cache"
        project_cache.mkdir()
        (project_cache / "new_target.txt").write_text("NEW TARGET\n")
        (project_cache / "shared_link").symlink_to("new_target.txt")
        (project_cache / "file_over_link").write_text("PROJECT FILE\n")
        (project_cache / "link_over_file").symlink_to("new_target.txt")

        self.config = SimpleNamespace(name="test-repo", template_cow_patterns=[".cache"])
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        dest_cache = worktree_dir / ".cache"
        self.assertTrue((dest_cache / "shared_link").is_symlink())
        self.assertEqual((dest_cache / "shared_link").readlink(), Path("new_target.txt"))
        self.assertFalse((dest_cache / "file_over_link").is_symlink())
        self.assertEqual((dest_cache / "file_over_link").read_text(), "PROJECT FILE\n")
        self.assertEqual((dest_cache / "target.txt").read_text(), "ORIGINAL TARGET\n")
        self.assertTrue((dest_cache / "link_over_file").is_symlink())
        self.assertEqual((dest_cache / "link_over_file").readlink(), Path("new_target.txt"))

    def test_copy__reads_cow_and_raw_patterns_from_real_toml_configs(self):
        """Test end-to-end COW and RAW pattern loading from real global and project TOML files."""
        global_config_path = self.global_dir / "config.toml"
        global_config_path.write_text(
            'type = "global"\n'
            "allowed_envvars = []\n"
            "\n"
            "[templates]\n"
            'cow = [".cache", "data/blobs/*.db"]\n'
            'raw = ["*.raw", "scripts/*.sh"]\n'
        )
        allowed_hooks = AllowedHooksConfig(JsonConfigIO(self.global_dir / "allowed_hooks.json"))
        global_config = GlobalConfig(TomlConfigIO(global_config_path), allowed_hooks)
        global_config.load()

        project_config_path = self.project_dir / ".gh" / "worktree" / "config.toml"
        project_config_path.write_text(
            'type = "repository"\n'
            'name = "toml-repo"\n'
            "\n"
            "[templates]\n"
            'cow = ["nested/vendor"]\n'
            'raw = ["docs/literal/*.md"]\n'
        )
        project_config = RepositoryConfig(TomlConfigIO(project_config_path))
        project_config.load()

        (self.global_templates_dir / ".cache").mkdir()
        (self.global_templates_dir / ".cache" / "entry.txt").write_text("CACHE: $REPO_NAME\n")
        (self.global_templates_dir / "data" / "blobs").mkdir(parents=True)
        (self.global_templates_dir / "data" / "blobs" / "app.db").write_text("DB: $REPO_NAME\n")
        (self.global_templates_dir / "notes.raw").write_text("RAW: $REPO_NAME\n")
        (self.global_templates_dir / "scripts").mkdir()
        (self.global_templates_dir / "scripts" / "run.sh").write_text("echo $WORKTREE_NAME\n")

        (self.project_templates_dir / "nested" / "vendor" / "pkg").mkdir(parents=True)
        (self.project_templates_dir / "nested" / "vendor" / "pkg" / "index.txt").write_text(
            "VENDOR: $WORKTREE_NAME\n"
        )
        (self.project_templates_dir / "docs" / "literal").mkdir(parents=True)
        (self.project_templates_dir / "docs" / "literal" / "guide.md").write_text(
            "GUIDE: $REPO_NAME\n"
        )

        self.global_config = global_config
        self.config = project_config
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        original_cow_copy = CowTemplate.copy
        original_raw_copy = RawTemplate.copy
        with (
            patch.object(
                CowTemplate, "copy", autospec=True, side_effect=original_cow_copy
            ) as mock_cow_copy,
            patch.object(
                RawTemplate, "copy", autospec=True, side_effect=original_raw_copy
            ) as mock_raw_copy,
        ):
            templates.copy("my-worktree")

        cow_dests = {
            call.args[2].relative_to(worktree_dir) for call in mock_cow_copy.call_args_list
        }
        raw_dests = {
            call.args[2].relative_to(worktree_dir) for call in mock_raw_copy.call_args_list
        }
        self.assertEqual(
            cow_dests,
            {Path(".cache"), Path("data/blobs/app.db"), Path("nested/vendor")},
        )
        self.assertEqual(
            raw_dests,
            {Path("notes.raw"), Path("scripts/run.sh"), Path("docs/literal/guide.md")},
        )

        self.assertEqual((worktree_dir / ".cache" / "entry.txt").read_text(), "CACHE: $REPO_NAME\n")
        self.assertEqual(
            (worktree_dir / "data" / "blobs" / "app.db").read_text(), "DB: $REPO_NAME\n"
        )
        self.assertEqual(
            (worktree_dir / "nested" / "vendor" / "pkg" / "index.txt").read_text(),
            "VENDOR: $WORKTREE_NAME\n",
        )
        self.assertEqual((worktree_dir / "notes.raw").read_text(), "RAW: $REPO_NAME\n")
        self.assertEqual((worktree_dir / "scripts" / "run.sh").read_text(), "echo $WORKTREE_NAME\n")
        self.assertEqual(
            (worktree_dir / "docs" / "literal" / "guide.md").read_text(), "GUIDE: $REPO_NAME\n"
        )
        self.assertIn("REPO: toml-repo", (worktree_dir / "config.txt").read_text())

    def test_copy__propagates_config_type_error_for_malformed_templates_toml(self):
        """Test that malformed [templates] TOML raises ConfigTypeError during Templates.copy."""
        project_config_path = self.project_dir / ".gh" / "worktree" / "config.toml"
        project_config_path.write_text(
            'type = "repository"\nname = "toml-repo"\n\n[templates]\ncow = "*.db"\n'
        )
        project_config = RepositoryConfig(TomlConfigIO(project_config_path))
        project_config.load()

        self.config = project_config
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        with self.assertRaises(ConfigTypeError):
            templates.copy("my-worktree")

    def test_copy__cow_file_and_directory_end_to_end_with_real_cp(self):
        """Test real platform cp execution for COW file, symlink, and directory templates."""
        scripts_dir = self.project_templates_dir / "scripts"
        scripts_dir.mkdir()
        cow_file = scripts_dir / "tool.bin"
        cow_file.write_bytes(b"#!/bin/sh\necho $REPO_NAME\n\x00BIN")
        cow_file.chmod(0o755)

        (self.project_templates_dir / "cow_link.db").symlink_to("env.txt")

        cow_dir = self.project_templates_dir / "cache"
        (cow_dir / "sub").mkdir(parents=True)
        (cow_dir / "data.txt").write_text("CACHE: $WORKTREE_NAME\n")
        cow_script = cow_dir / "run.sh"
        cow_script.write_text("#!/bin/sh\necho $REPO_NAME\n")
        cow_script.chmod(0o755)
        (cow_dir / "sub" / "item.bin").write_bytes(b"\x89PNG\x00$REPO_NAME")
        (cow_dir / "latest").symlink_to("data.txt")

        self.config = SimpleNamespace(
            name="test-repo",
            template_cow_patterns=["scripts/tool.bin", "cow_link.db", "cache"],
        )
        templates = Templates(self._create_context(), Mock())

        worktree_dir = self.project_dir / "my-worktree"
        worktree_dir.mkdir(parents=True)

        templates.copy("my-worktree")

        copied_file = worktree_dir / "scripts" / "tool.bin"
        self.assertEqual(copied_file.read_bytes(), b"#!/bin/sh\necho $REPO_NAME\n\x00BIN")
        self.assertEqual(copied_file.stat().st_mode & 0o777, 0o755)

        copied_link = worktree_dir / "cow_link.db"
        self.assertTrue(copied_link.is_symlink())
        self.assertEqual(copied_link.readlink(), Path("env.txt"))

        copied_dir = worktree_dir / "cache"
        self.assertEqual((copied_dir / "data.txt").read_text(), "CACHE: $WORKTREE_NAME\n")
        self.assertEqual((copied_dir / "run.sh").read_text(), "#!/bin/sh\necho $REPO_NAME\n")
        self.assertEqual((copied_dir / "run.sh").stat().st_mode & 0o777, 0o755)
        self.assertEqual((copied_dir / "sub" / "item.bin").read_bytes(), b"\x89PNG\x00$REPO_NAME")
        self.assertTrue((copied_dir / "latest").is_symlink())
        self.assertEqual((copied_dir / "latest").readlink(), Path("data.txt"))

        if shutil.which("cp") is not None:
            debug_messages = [call.args[0] for call in templates.logger.debug.call_args_list]
            self.assertFalse(
                any("falling back to shutil" in msg for msg in debug_messages),
                f"Expected real cp to succeed without fallback, got: {debug_messages}",
            )

    def test_add__creates_file(self):
        context = self._create_context()
        templates = Templates(context, Mock())

        relative_path = ".gh/worktree/templates/example.txt"
        with templates.add(relative_path) as f:
            f.write("hello")

        template_file = self.project_dir / relative_path
        self.assertTrue(template_file.exists())
        self.assertEqual(template_file.read_text(), "hello")

    def test_add__raises_when_existing(self):
        context = self._create_context()
        templates = Templates(context, Mock())

        relative_path = ".gh/worktree/templates/example.txt"
        template_file = self.project_dir / relative_path
        template_file.parent.mkdir(parents=True, exist_ok=True)
        template_file.write_text("existing")

        with self.assertRaisesRegex(TemplateExists, "Template .* already exists"):
            with templates.add(relative_path) as f:
                f.write("new")

    def sync_setup(self):
        """Return a Templates instance and a mocked GitCLI for sync tests."""
        context = self._create_context()
        templates = Templates(context, Mock())
        git = Mock()
        git.ls_tree_entries.return_value = []
        git.read_blob.return_value = b""
        return templates, git

    def entry(self, path, mode="100644", obj_type="blob", sha="abc123"):
        return GitTreeEntry(mode, obj_type, sha, path)

    def test_sync_from_git__writes_binary_content_byte_for_byte(self):
        templates, git = self.sync_setup()
        payload = b"\x89PNG\r\n\x1a\n\x00\x00\xff\xfe"
        git.ls_tree_entries.return_value = [self.entry(".treefort/templates/logo.png")]
        git.read_blob.return_value = payload

        synced = templates.sync_from_git(git, "origin/main")

        template_file = self.project_templates_dir / "logo.png"
        self.assertEqual(1, synced)
        self.assertEqual(payload, template_file.read_bytes())
        git.ls_tree_entries.assert_called_once_with("origin/main", ".treefort/templates")
        git.read_blob.assert_called_once_with("abc123")

    def test_sync_from_git__preserves_executable_and_plain_modes(self):
        templates, git = self.sync_setup()
        git.ls_tree_entries.return_value = [
            self.entry(".treefort/templates/script.sh", "100755"),
            self.entry(".treefort/templates/config.toml", "100644"),
        ]
        git.read_blob.return_value = b"content"

        synced = templates.sync_from_git(git, "origin/main")

        self.assertEqual(2, synced)
        self.assertEqual(0o755, (self.project_templates_dir / "script.sh").stat().st_mode & 0o777)
        self.assertEqual(0o644, (self.project_templates_dir / "config.toml").stat().st_mode & 0o777)

    def test_sync_from_git__creates_nested_directories(self):
        templates, git = self.sync_setup()
        git.ls_tree_entries.return_value = [self.entry(".treefort/templates/deep/nested/file.txt")]
        git.read_blob.return_value = b"nested"

        synced = templates.sync_from_git(git, "origin/main")

        self.assertEqual(1, synced)
        self.assertEqual(
            b"nested", (self.project_templates_dir / "deep" / "nested" / "file.txt").read_bytes()
        )

    def test_sync_from_git__overwrite_false_skips_existing_file(self):
        templates, git = self.sync_setup()
        git.ls_tree_entries.return_value = [self.entry(".treefort/templates/env.txt")]
        git.read_blob.return_value = b"upstream"

        synced = templates.sync_from_git(git, "origin/main", overwrite=False)

        self.assertEqual(0, synced)
        self.assertEqual(
            "PATH: $WORKTREE_DIR\nUSER: $USER\n",
            (self.project_templates_dir / "env.txt").read_text(),
        )
        git.read_blob.assert_not_called()
        templates.logger.warning.assert_called_once()
        self.assertIn("already exists", templates.logger.warning.call_args.args[0])

    def test_sync_from_git__overwrite_true_replaces_existing_file(self):
        templates, git = self.sync_setup()
        git.ls_tree_entries.return_value = [self.entry(".treefort/templates/env.txt")]
        git.read_blob.return_value = b"upstream"

        synced = templates.sync_from_git(git, "origin/main")

        self.assertEqual(1, synced)
        self.assertEqual(b"upstream", (self.project_templates_dir / "env.txt").read_bytes())

    def test_sync_from_git__skips_symlink_and_non_blob_entries(self):
        templates, git = self.sync_setup()
        git.ls_tree_entries.return_value = [
            self.entry(".treefort/templates/link", "120000"),
            self.entry(".treefort/templates/tree", obj_type="tree"),
        ]

        synced = templates.sync_from_git(git, "origin/main")

        self.assertEqual(0, synced)
        git.read_blob.assert_not_called()

    def test_sync_from_git__raises_validation_error_on_parent_traversal(self):
        templates, git = self.sync_setup()
        git.ls_tree_entries.return_value = [self.entry(".treefort/templates/../../evil")]

        with self.assertRaises(ValidationError):
            templates.sync_from_git(git, "origin/main")

        self.assertFalse((self.project_dir / "evil").exists())
        git.read_blob.assert_not_called()

    def test_sync_from_git__raises_validation_error_on_empty_relative_path(self):
        templates, git = self.sync_setup()
        git.ls_tree_entries.return_value = [self.entry(".treefort/templates/")]

        with self.assertRaises(ValidationError):
            templates.sync_from_git(git, "origin/main")

        git.read_blob.assert_not_called()

    def test_sync_from_git__reports_a_local_file_blocking_a_new_directory(self):
        templates, git = self.sync_setup()
        (self.project_templates_dir / "foo").write_bytes(b"stale file")
        git.ls_tree_entries.return_value = [self.entry(".treefort/templates/foo/bar.txt")]
        git.read_blob.return_value = b"new"

        with self.assertRaisesRegex(ValidationError, "local file or directory is in the way"):
            templates.sync_from_git(git, "origin/main")

        self.assertEqual(b"stale file", (self.project_templates_dir / "foo").read_bytes())

    def test_sync_from_git__reports_a_local_directory_blocking_a_new_file(self):
        templates, git = self.sync_setup()
        (self.project_templates_dir / "foo").mkdir()
        git.ls_tree_entries.return_value = [self.entry(".treefort/templates/foo")]
        git.read_blob.return_value = b"new"

        with self.assertRaisesRegex(ValidationError, "local file or directory is in the way"):
            templates.sync_from_git(git, "origin/main")

        self.assertTrue((self.project_templates_dir / "foo").is_dir())
