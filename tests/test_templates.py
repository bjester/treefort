import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from treefort.errors import ValidationError
from treefort.git import GitTreeEntry
from treefort.templates import TemplateExists, Templates


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
