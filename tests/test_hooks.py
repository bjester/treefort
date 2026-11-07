import hashlib
import os
import stat
import tempfile
from pathlib import Path
from unittest import TestCase, mock
from unittest.mock import Mock

from treefort.context import Context
from treefort.errors import HookError, HookExistsError
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
