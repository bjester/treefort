import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from treefort.errors import AncestorNotFoundError, ValidationError
from treefort.utils import (
    find_up,
    normalize_worktree_name,
    resolve_contained,
    write_bytes_atomically,
)


class FindUpTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_find_up_success(self):
        # Create a directory structure:
        # root/
        #   target_file
        #   subdir/
        #     subsubdir/
        root = self.tmp_path
        (root / "target_file").touch()
        subdir = root / "subdir"
        subdir.mkdir()
        subsubdir = subdir / "subsubdir"
        subsubdir.mkdir()

        # Test finding from subsubdir
        found_path = find_up("target_file", subsubdir)
        self.assertEqual(found_path, root / "target_file")

        # Test finding from root
        found_path = find_up("target_file", root)
        self.assertEqual(found_path, root / "target_file")

    def test_find_up_failure(self):
        # Create a directory structure without the target file
        root = self.tmp_path
        subdir = root / "subdir"
        subdir.mkdir()

        with self.assertRaisesRegex(AncestorNotFoundError, "Could not find non_existent_file"):
            find_up("non_existent_file", subdir)


class NormalizeWorktreeNameTestCase(TestCase):
    def test_simple_name_unchanged(self):
        self.assertEqual(normalize_worktree_name("feature"), "feature")

    def test_slash_replaced_with_dash(self):
        self.assertEqual(normalize_worktree_name("feat/some-feature"), "feat-some-feature")

    def test_multiple_slashes_collapsed(self):
        self.assertEqual(normalize_worktree_name("feat/nested/branch"), "feat-nested-branch")

    def test_dashes_preserved(self):
        self.assertEqual(normalize_worktree_name("my-feature-branch"), "my-feature-branch")

    def test_underscores_replaced(self):
        self.assertEqual(normalize_worktree_name("my_feature_branch"), "my-feature-branch")

    def test_mixed_slashes_and_dashes(self):
        self.assertEqual(normalize_worktree_name("feat/my-feature"), "feat-my-feature")

    def test_numbers_preserved(self):
        self.assertEqual(normalize_worktree_name("feature-123"), "feature-123")

    def test_special_chars_replaced(self):
        self.assertEqual(normalize_worktree_name("feat@branch#1"), "feat-branch-1")

    def test_consecutive_special_chars_collapsed(self):
        self.assertEqual(normalize_worktree_name("feat//branch"), "feat-branch")

    def test_consecutive_mixed_special_chars_collapsed(self):
        self.assertEqual(normalize_worktree_name("feat/@/#branch"), "feat-branch")


class WriteBytesAtomicallyTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_write_bytes_atomically__writes_content_and_mode(self):
        dest = self.tmp_path / "nested" / "file.bin"
        payload = b"\x00\x01\xff"

        write_bytes_atomically(dest, payload, 0o755)

        self.assertEqual(payload, dest.read_bytes())
        self.assertEqual(0o755, dest.stat().st_mode & 0o777)

    def test_write_bytes_atomically__replaces_existing_content(self):
        dest = self.tmp_path / "file.txt"
        dest.write_bytes(b"before")

        write_bytes_atomically(dest, b"after", 0o644)

        self.assertEqual(b"after", dest.read_bytes())

    def test_write_bytes_atomically__leaves_no_temporary_files(self):
        dest = self.tmp_path / "file.txt"

        write_bytes_atomically(dest, b"content", 0o644)

        self.assertEqual([dest], list(self.tmp_path.iterdir()))

    def test_write_bytes_atomically__keeps_destination_when_replace_fails(self):
        dest = self.tmp_path / "file.txt"
        dest.write_bytes(b"before")

        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_bytes_atomically(dest, b"after", 0o644)

        self.assertEqual(b"before", dest.read_bytes())
        self.assertEqual([dest], list(self.tmp_path.iterdir()))

    def test_write_bytes_atomically__reports_a_file_blocking_a_directory(self):
        blocker = self.tmp_path / "blocker"
        blocker.write_bytes(b"i am a file")

        with self.assertRaisesRegex(ValidationError, "local file or directory is in the way"):
            write_bytes_atomically(blocker / "nested" / "file.txt", b"content", 0o644)

        self.assertEqual(b"i am a file", blocker.read_bytes())

    def test_write_bytes_atomically__reports_a_directory_blocking_a_file(self):
        blocker = self.tmp_path / "blocker"
        blocker.mkdir()
        (blocker / "keep.txt").write_bytes(b"kept")

        with self.assertRaisesRegex(ValidationError, "local file or directory is in the way"):
            write_bytes_atomically(blocker, b"content", 0o644)

        self.assertTrue(blocker.is_dir())
        self.assertEqual(b"kept", (blocker / "keep.txt").read_bytes())
        self.assertEqual([blocker], list(self.tmp_path.iterdir()))


class ResolveContainedTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.base_dir = self.tmp_path / "base"
        self.base_dir.mkdir()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_resolve_contained__returns_a_direct_child(self):
        self.assertEqual(self.base_dir / "file.txt", resolve_contained(self.base_dir, "file.txt"))

    def test_resolve_contained__permits_nested_paths(self):
        self.assertEqual(
            self.base_dir / "deep" / "nested" / "file.txt",
            resolve_contained(self.base_dir, "deep/nested/file.txt"),
        )

    def test_resolve_contained__permits_a_name_that_starts_with_dots(self):
        self.assertEqual(self.base_dir / "..foo", resolve_contained(self.base_dir, "..foo"))

    def test_resolve_contained__rejects_an_empty_path(self):
        with self.assertRaisesRegex(ValidationError, "escapes"):
            resolve_contained(self.base_dir, "")

    def test_resolve_contained__rejects_the_base_directory_itself(self):
        with self.assertRaisesRegex(ValidationError, "escapes"):
            resolve_contained(self.base_dir, ".")

    def test_resolve_contained__rejects_a_parent_traversal(self):
        with self.assertRaisesRegex(ValidationError, "escapes"):
            resolve_contained(self.base_dir, "../evil")

    def test_resolve_contained__rejects_a_nested_traversal(self):
        with self.assertRaisesRegex(ValidationError, "escapes"):
            resolve_contained(self.base_dir, "nested/../../evil")
