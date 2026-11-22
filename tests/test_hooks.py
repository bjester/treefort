import hashlib
import os
import stat
import tempfile
from pathlib import Path
from unittest import TestCase, mock
from unittest.mock import Mock

from treefort.context import Context
from treefort.errors import HookError, HookExistsError, ValidationError
from treefort.git import GitTreeEntry
from treefort.hooks import Hook, Hooks


class HooksTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        # isolate the global configuration so tests never write to the developer's ~/.treefort
        self.home_dir = self.tmp_path / "home"
        self.home_dir.mkdir()
        home_patcher = mock.patch.dict(os.environ, {"HOME": str(self.home_dir)})
        home_patcher.start()
        self.addCleanup(home_patcher.stop)

        self.context = Context()
        self.context.cwd = self.tmp_path
        self.hooks_path = self.tmp_path / ".treefort" / "hooks"
        (self.tmp_path / ".bare").mkdir()
        self.context.config_dir.mkdir(parents=True, exist_ok=True)
        self.hooks_path.mkdir()

        self.hooks = Hooks(self.context, Mock())

    def tearDown(self):
        self.tmp_dir.cleanup()

    @mock.patch("treefort.hooks.input")
    def test_check_allowed__existing_hook(self, mock_input):
        global_config = self.context.get_global_config()

        hook_file = self.hooks_path / Hook.pre_init.name
        hook_file.write_bytes(b"echo ok")
        checksum = hashlib.sha256(hook_file.read_bytes()).hexdigest()

        global_config.allow_hook(str(hook_file), checksum)

        self.assertTrue(self.hooks._check_allowed(hook_file))

        mock_input.assert_not_called()
        self.assertEqual(global_config.allowed_hooks.get(str(hook_file)), checksum)

    @mock.patch("treefort.hooks.input")
    def test_check_allowed__prompts_and_saves(self, mock_input):
        hook_file = self.hooks_path / Hook.pre_init.name
        hook_file.write_bytes(b"echo ok")
        checksum = hashlib.sha256(hook_file.read_bytes()).hexdigest()

        mock_input.return_value = "y"

        self.assertTrue(self.hooks._check_allowed(hook_file))

        mock_input.assert_called_once()
        global_config = self.context.get_global_config()
        self.assertEqual(global_config.allowed_hooks.get(str(hook_file)), checksum)

    @mock.patch.object(Hooks, "stream_exec")
    @mock.patch.object(Hooks, "_check_allowed")
    def test_fire__allowed(self, mock_check_allowed, mock_stream_exec):
        hook_file = self.hooks_path / Hook.pre_init.name
        hook_file.write_bytes(b"echo ok")
        # Make the hook file executable
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

        mock_check_allowed.return_value = True
        mock_stream_exec.return_value = 0

        self.assertTrue(self.hooks.fire(Hook.pre_init, "arg1"))
        mock_stream_exec.assert_called_once_with([str(hook_file), "arg1"], stream_info=True)

    @mock.patch.object(Hooks, "stream_exec")
    @mock.patch.object(Hooks, "_check_allowed")
    def test_fire__logs_step_header_before_streaming(self, mock_check_allowed, mock_stream_exec):
        hook_file = self.hooks_path / Hook.pre_init.name
        hook_file.write_bytes(b"echo ok")
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

        events = []
        mock_check_allowed.return_value = True
        mock_stream_exec.side_effect = lambda *args, **kwargs: events.append("stream") or 0
        self.hooks.logger.info.side_effect = lambda *args, **kwargs: events.append("header")

        with mock.patch("treefort.ui.is_color_enabled", return_value=False):
            self.hooks.fire(Hook.pre_init)

        self.assertEqual(events, ["header", "stream"])
        self.hooks.logger.info.assert_called_once_with("◎ Running hook pre_init")

    @mock.patch.object(Hooks, "stream_exec")
    @mock.patch.object(Hooks, "_check_allowed")
    def test_fire__failure_includes_last_output_in_hook_error(
        self, mock_check_allowed, mock_stream_exec
    ):
        hook_file = self.hooks_path / Hook.pre_init.name
        hook_file.write_bytes(b"exit 2")
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

        mock_check_allowed.return_value = True
        mock_stream_exec.return_value = 2
        self.hooks.last_output = ["permission denied: /etc/hosts"]

        with self.assertRaises(HookError) as ctx:
            self.hooks.fire(Hook.pre_init, "arg1")

        self.assertIn("Hook pre_init failed with exit code 2", str(ctx.exception))
        self.assertIn("permission denied: /etc/hosts", str(ctx.exception))

    @mock.patch.object(Hooks, "_check_allowed")
    @mock.patch.object(Hooks, "stream_exec")
    def test_fire__not_executable(self, mock_stream_exec, mock_check_allowed):
        hook_file = self.hooks_path / Hook.pre_init.name
        hook_file.write_bytes(b"echo ok")
        hook_file.chmod(stat.S_IREAD)

        mock_check_allowed.return_value = True
        mock_stream_exec.return_value = 0

        self.assertFalse(self.hooks.fire(Hook.pre_init, "arg1"))
        mock_stream_exec.assert_not_called()

    @mock.patch.object(Hooks, "_check_allowed")
    @mock.patch.object(Hooks, "stream_exec")
    def test_fire__disallowed(self, mock_stream_exec, mock_check_allowed):
        hook_file = self.hooks_path / Hook.pre_init.name
        hook_file.write_bytes(b"echo ok")
        # Make the hook file executable
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IEXEC)

        mock_check_allowed.return_value = False
        mock_stream_exec.return_value = 0

        self.assertFalse(self.hooks.fire(Hook.pre_init, "arg1"))
        mock_stream_exec.assert_not_called()
        self.hooks.logger.info.assert_not_called()

    def test_add(self):
        with self.hooks.add(Hook.pre_init) as f:
            f.write("echo ok")

        hook_file = self.hooks_path / Hook.pre_init.name
        self.assertTrue(hook_file.exists())
        self.assertEqual(hook_file.read_text(), "echo ok")
        self.assertTrue(os.access(str(hook_file), os.X_OK))

    def test_add__existing(self):
        hook_file = self.hooks_path / Hook.pre_init.name
        hook_file.write_bytes(b"echo ok")

        with self.assertRaises(HookExistsError):
            with self.hooks.add(Hook.pre_init) as f:
                f.write("echo ok")


class HooksSyncFromGitTestCase(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        self.home_dir = self.tmp_path / "home"
        self.home_dir.mkdir()
        home_patcher = mock.patch.dict(os.environ, {"HOME": str(self.home_dir)})
        home_patcher.start()
        self.addCleanup(home_patcher.stop)

        self.context = Context()
        self.context.cwd = self.tmp_path
        (self.tmp_path / ".bare").mkdir()
        self.hooks_path = self.tmp_path / ".treefort" / "hooks"

        self.hooks = Hooks(self.context, Mock())
        self.git = Mock()
        self.git.ls_tree_entries.return_value = []
        self.git.read_blob.return_value = b""

    def tearDown(self):
        self.tmp_dir.cleanup()

    def entry(self, path, mode="100644", obj_type="blob", sha="abc123"):
        return GitTreeEntry(mode, obj_type, sha, path)

    def test_sync_from_git__writes_binary_content_and_executable_mode(self):
        payload = b"#!/bin/sh\x00\x00echo ok\xff"
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/pre_init", "100755")]
        self.git.read_blob.return_value = payload

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        hook_file = self.hooks_path / "pre_init"
        self.assertEqual(1, synced)
        self.assertEqual(payload, hook_file.read_bytes())
        self.assertEqual(0o755, hook_file.stat().st_mode & 0o777)
        self.git.ls_tree_entries.assert_called_once_with("origin/main", ".treefort/hooks")
        self.git.read_blob.assert_called_once_with("abc123")

    def test_sync_from_git__overwrite_false_skips_existing_file(self):
        hook_file = self.hooks_path / "pre_init"
        self.hooks_path.mkdir(parents=True)
        hook_file.write_bytes(b"local hook")
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/pre_init")]

        synced = self.hooks.sync_from_git(self.git, "origin/main", overwrite=False)

        self.assertEqual(0, synced)
        self.assertEqual(b"local hook", hook_file.read_bytes())
        self.git.read_blob.assert_not_called()
        self.hooks.logger.warning.assert_called_once()
        self.assertIn("already exists", self.hooks.logger.warning.call_args.args[0])

    def test_sync_from_git__overwrite_true_replaces_existing_file(self):
        hook_file = self.hooks_path / "pre_init"
        self.hooks_path.mkdir(parents=True)
        hook_file.write_bytes(b"local hook")
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/pre_init")]
        self.git.read_blob.return_value = b"upstream hook"

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(1, synced)
        self.assertEqual(b"upstream hook", hook_file.read_bytes())

    def test_sync_from_git__skips_unknown_and_nested_hook_names(self):
        self.git.ls_tree_entries.return_value = [
            self.entry(".treefort/hooks/custom_hook"),
            self.entry(".treefort/hooks/nested/pre_init"),
        ]

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(0, synced)
        self.git.read_blob.assert_not_called()
        self.assertFalse(self.hooks_path.exists())

    def test_sync_from_git__skips_symlink_and_non_blob_entries(self):
        self.git.ls_tree_entries.return_value = [
            self.entry(".treefort/hooks/pre_init", "120000"),
            self.entry(".treefort/hooks/post_init", obj_type="tree"),
        ]

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(0, synced)
        self.git.read_blob.assert_not_called()

    def test_sync_from_git__skips_paths_outside_the_hooks_prefix(self):
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/templates/pre_init")]

        synced = self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(0, synced)
        self.git.read_blob.assert_not_called()

    def test_sync_from_git__raises_validation_error_on_parent_traversal(self):
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/../evil")]

        with self.assertRaises(ValidationError):
            self.hooks.sync_from_git(self.git, "origin/main")

        self.assertFalse((self.tmp_path / ".treefort" / "evil").exists())
        self.git.read_blob.assert_not_called()

    def test_sync_from_git__raises_validation_error_when_path_resolves_outside(self):
        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/nested/../../evil")]

        with self.assertRaises(ValidationError):
            self.hooks.sync_from_git(self.git, "origin/main")

        self.assertFalse((self.tmp_path / ".treefort" / "evil").exists())
        self.git.read_blob.assert_not_called()

    def test_sync_from_git__leaves_allowed_hooks_untouched(self):
        allowed_hooks_file = self.home_dir / ".treefort" / "allowed_hooks.json"
        self.context.get_global_config()
        before = allowed_hooks_file.read_bytes()

        self.git.ls_tree_entries.return_value = [self.entry(".treefort/hooks/pre_init", "100755")]
        self.git.read_blob.return_value = b"#!/bin/sh\necho ok\n"

        self.hooks.sync_from_git(self.git, "origin/main")

        self.assertEqual(before, allowed_hooks_file.read_bytes())
        global_config = self.context.get_global_config()
        self.assertEqual(
            "", global_config.allowed_hooks.get(str(self.hooks_path / "pre_init"), default="")
        )
